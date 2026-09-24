"""Bounded-memory PAE conversion and isolated domain analysis.

The stdlib JSON decoder is fed one matrix row at a time. Unused matrices are
skipped without decoding their numbers. NumPy is imported only by these APIs.
"""
import contextlib
import json
import os
import re
import sys
import time
from pathlib import Path
import af3_runtime as R


class JsonStream:
    def __init__(self, stream):
        self.stream = stream; self.buf = ""; self.pos = 0; self.eof = False
        self.decoder = json.JSONDecoder()

    def fill(self):
        chunk = self.stream.read(131072)
        self.buf = self.buf[self.pos:] + chunk; self.pos = 0
        self.eof = not chunk

    def peek(self):
        while True:
            self.pos = re.compile(r"\s*").match(self.buf, self.pos).end()
            if self.pos < len(self.buf): return self.buf[self.pos]
            if self.eof: raise ValueError("JSON 意外结束")
            self.fill()

    def take(self, ch):
        if self.peek() != ch: raise ValueError("JSON 应为 " + ch)
        self.pos += 1

    def value(self):
        self.peek()
        while True:
            try:
                value, end = self.decoder.raw_decode(self.buf, self.pos)
                self.pos = end; return value
            except ValueError:
                if self.eof: raise
                self.fill()

    def skip(self):
        ch = self.peek()
        if ch not in "[{": self.value(); return
        depth = 0
        while True:
            match = re.search(r'[\[\]{}"]', self.buf[self.pos:])
            if match is None:
                self.pos = len(self.buf)
                if self.eof: raise ValueError("JSON 容器未关闭")
                self.fill(); continue
            self.pos += match.start(); ch = self.buf[self.pos]
            if ch == '"': self.value(); continue
            self.pos += 1
            if ch in "[{": depth += 1
            else:
                depth -= 1
                if depth == 0: return


def load(path, cache_dir=None):
    """Return a read-only float64 memmap and token metadata; no precision loss."""
    import numpy as np
    source = Path(path).resolve(); st = source.stat()
    key = R.digest([str(source), st.st_size, st.st_mtime_ns, "pae-f64-v1"], 32)
    root = Path(cache_dir or os.environ.get("AF3_PAE_CACHE") or (Path.home() / ".cache" / "af3_console" / "pae"))
    root.mkdir(parents=True, exist_ok=True)
    matrix_path = root / (key + ".npy"); meta_path = root / (key + ".json")
    with R.file_lock(str(meta_path) + ".lock"):
        meta = R.read_json(meta_path)
        if not matrix_path.exists() or not meta:
            temporary = root / (key + "." + str(os.getpid()) + ".npy.tmp")
            meta = {"source": str(source), "size": st.st_size, "mtime_ns": st.st_mtime_ns}
            matrix = None
            try:
                with source.open(encoding="utf-8-sig") as file:
                    reader = JsonStream(file); reader.take("{")
                    while reader.peek() != "}":
                        field = reader.value(); reader.take(":")
                        if field in ("pae", "predicted_aligned_error"):
                            reader.take("["); row = reader.value()
                            n = len(row)
                            if not n: raise ValueError("PAE 为空")
                            matrix = np.lib.format.open_memmap(temporary, mode="w+", dtype="float64", shape=(n,n))
                            count = 0
                            while True:
                                if len(row) != n or count >= n: raise ValueError("PAE 不是方阵")
                                values = np.asarray(row, dtype="float64")
                                if not np.isfinite(values).all(): raise ValueError("PAE 含非有限值")
                                matrix[count] = values; count += 1
                                if reader.peek() == "]": reader.take("]"); break
                                reader.take(","); row = reader.value()
                            if count != n: raise ValueError("PAE 行数错误")
                            meta["n"] = n
                        elif field in ("token_chain_ids", "token_res_ids", "token_residue_ids"):
                            meta[field] = reader.value()
                        else: reader.skip()
                        if reader.peek() == "}": break
                        reader.take(",")
                    reader.take("}")
                if matrix is None: raise ValueError("未找到 PAE 矩阵")
                matrix.flush(); del matrix; matrix = None
                after = source.stat()
                if (after.st_size, after.st_mtime_ns) != (st.st_size, st.st_mtime_ns):
                    raise ValueError("读取期间文件发生变化，请稍后重试")
                os.replace(temporary, matrix_path)
                R.atomic_json(meta_path, meta)
            finally:
                if matrix is not None: del matrix
                if temporary.exists(): temporary.unlink()
    # Budget disk cache, keeping the currently opened entry. Memmaps don't retain
    # Python float matrices; deletion of in-use mappings is skipped on Windows.
    _trim(root, matrix_path)
    return np.load(matrix_path, mmap_mode="r", allow_pickle=False), meta


def _trim(root, keep, budget=2*1024**3):
    entries = sorted(root.glob("*.npy"), key=lambda p:p.stat().st_mtime, reverse=True)
    size = keep.stat().st_size
    for entry in entries:
        if entry == keep: continue
        size += entry.stat().st_size
        if size > budget:
            try:
                entry.unlink()
                entry.with_suffix(".json").unlink(missing_ok=True)
            except OSError: pass


def chains(meta, input_data=None, labels=None):
    ids = meta.get("token_chain_ids")
    n = meta.get("n", 0)
    result = []
    if ids and len(ids) == n:
        start = 0
        for index in range(1, n+1):
            if index == n or ids[index] != ids[start]:
                cid = str(ids[start])
                result.append(dict(id=cid, label=cid, start=start, end=index, type="protein"))
                start = index
        types = {}; names = {}
        for seq in (input_data or {}).get("sequences", []):
            for kind, body in seq.items():
                seq_ids = body.get("id", []); seq_ids = seq_ids if isinstance(seq_ids,list) else [seq_ids]
                for cid in seq_ids: types[str(cid)] = kind
        for index, ch in enumerate(result):
            ch["type"] = types.get(ch["id"], "unknown")
            if labels and index < len(labels): ch["label"] = str(labels[index]) + " [" + ch["id"] + "]"
        return result
    # Legacy files: only infer boundaries if every token is a standard polymer
    # residue and the lengths exactly cover the matrix.
    pos = 0
    for seq in (input_data or {}).get("sequences", []):
        for kind, body in seq.items():
            if kind == "ligand" or body.get("modifications"): return []
            ids = body.get("id", []); ids = ids if isinstance(ids,list) else [ids]
            for cid in ids:
                length = len(body.get("sequence", ""))
                result.append(dict(id=str(cid),label=str(cid),start=pos,end=pos+length,type=kind)); pos += length
    return result if pos == n else []


def domains(request):
    import af3
    matrix, meta = load(request["path"], request.get("cache_dir"))
    output = {}
    for ch in request["chains"]:
        if ch.get("type") != "protein": continue
        start, end = ch["start"], ch["end"]
        # Modified residues may span multiple tokens: use residue IDs when known.
        ids = meta.get("token_res_ids") or meta.get("token_residue_ids")
        residues = ids[start:end] if ids and len(ids) == len(matrix) else list(range(1,end-start+1))
        block=matrix[start:end,start:end]
        unique=[];seen=set()
        for index,residue in enumerate(residues):
            if residue not in seen: unique.append(index);seen.add(residue)
        analysis_residues=[residues[i] for i in unique]
        if len(unique)!=len(residues):
            import numpy as np
            block=block[np.ix_(unique,unique)]
        windows, ds, notes = af3._pae_fragment_windows(block, analysis_residues, **request["params"])
        lines = []
        for window in windows:
            residue_end = window["end"]
            offsets = [i for i,r in enumerate(residues) if r == residue_end]
            if offsets and offsets[-1]+1 < end-start:
                lines.append((start+offsets[-1]+1, residue_end))
        output[ch["label"]] = dict(lines=lines,windows=windows,domains=ds,notes=notes,s0=start,e0=end)
    return output


def render(request):
    """Render entirely in this isolated process; no Qt or shared pyplot state."""
    import numpy as np
    import base64, io
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.colors import LinearSegmentedColormap
    matrix=np.load(request['matrix'],allow_pickle=False)
    ds=request.get('ds',1);n=len(matrix)
    fig=Figure(figsize=(6,5),dpi=100);FigureCanvasAgg(fig);ax=fig.add_subplot(111)
    cmap=LinearSegmentedColormap.from_list('pae',[(0,'#0f17fc'),(5/30,'#5b89ee'),(10/30,'#fff400'),(15/30,'#ffae00'),(20/30,'#ababab'),(1,'#ffffff')])
    im=ax.imshow(matrix,cmap=cmap,vmin=0,vmax=30,aspect='auto')
    for ch in (request.get('chains') or [])[1:]:
        ax.axvline(ch['start']/ds-.5,color='black',lw=1);ax.axhline(ch['start']/ds-.5,color='black',lw=1)
    for pos,label in request.get('lines') or []:
        lp=pos/ds-.5
        ax.axvline(lp,color='red',ls='--',lw=1.2);ax.axhline(lp,color='red',ls='--',lw=1.2)
        ax.text(lp,-.012*n,label,color='red',fontsize=7,rotation=90,va='top',ha='right',clip_on=False)
    if ds>1:
        from matplotlib.ticker import FuncFormatter
        fmt=FuncFormatter(lambda v,_:str(int(round(v*ds))+1) if v>=-.5 else '')
        ax.xaxis.set_major_formatter(fmt);ax.yaxis.set_major_formatter(fmt)
    ax.set_title(request.get('title') or (f'PAE ({n*ds} × {n*ds}'+(f'; 1/{ds}' if ds>1 else '')+')'))
    fig.colorbar(im,ax=ax,label='PAE (Å)');fig.tight_layout();fig.canvas.draw();p=ax.get_position()
    buf=io.BytesIO();fig.savefig(buf,format='png',dpi=120)
    return dict(png=base64.b64encode(buf.getvalue()).decode(),axes=[p.x0,p.y0,p.width,p.height])


def execute(request):
    action=request.get('action','domains')
    if action=='render': return render(request)
    if action=='load':
        import numpy as np
        matrix,meta=load(request['path']);ds=max(1,(len(matrix)+1399)//1400)
        np.save(request['preview'],matrix[::ds,::ds],allow_pickle=False)
        mapping=chains(meta,R.read_json(request.get('input') or '',{}),request.get('labels'))
        return dict(ds=ds,chains=mapping)
    return domains(request)


if __name__ == "__main__":
    request = json.load(sys.stdin)
    try:
        # stdout is the worker's JSON protocol. Scientific helpers may print
        # diagnostics (e.g. an oversized domain retained after re-clustering).
        # Keep those on stderr; their structured notes remain in the answer.
        with contextlib.redirect_stdout(sys.stderr):
            answer = execute(request)
        json.dump(answer, sys.stdout, ensure_ascii=False)
    except Exception as exc:
        print(str(exc), file=sys.stderr); sys.exit(1)
