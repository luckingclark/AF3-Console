#!/usr/bin/env python3
"""Build one GUI artifact with isolated module namespaces."""
import ast
import base64
import hashlib
import json
import os
from pathlib import Path
import zlib

SOURCES = ["af3_ui_brand.py", "af3_ui_text.py", "af3_ui_help.py", "af3_ui_backend.py", "af3_ui_widgets.py", "af3_qt_app.py"]

def main():
    root=Path(__file__).resolve().parent
    sources={};hashes={}
    for name in SOURCES:
        source=(root/name).read_text(encoding="utf8")
        compile(source,name,"exec")
        sources[Path(name).stem]=base64.b64encode(zlib.compress(source.encode("utf8"),9)).decode()
        hashes[name]=hashlib.sha256(source.encode("utf8")).hexdigest()
    header="#!/usr/bin/env python3\n# merge_gui.py 自动生成；维护源文件，运行时各模块命名空间独立。\n"
    code=header+"import base64, zlib, types, sys, os\n"
    code+="sys.dont_write_bytecode = True\n_ROOT = os.path.dirname(os.path.abspath(__file__))\n"
    code+="sys.path.insert(0, _ROOT)\n_SOURCES = "+repr(sources)+"\nSOURCE_HASHES = "+repr(hashes)+"\n"
    code+="for _name, _packed in _SOURCES.items():\n    _module = types.ModuleType(_name)\n    _module.__file__ = os.path.join(_ROOT, _name + '.py')\n    sys.modules[_name] = _module\n    exec(compile(zlib.decompress(base64.b64decode(_packed)), _module.__file__, 'exec'), _module.__dict__)\n"
    code+="B = sys.modules['af3_ui_backend']\nH = sys.modules['af3_ui_help']\n_qt = sys.modules['af3_qt_app']\nApp = _qt.App\nif __name__ == '__main__':\n    _qt.main()\n"
    compile(code,"af3_gui","exec")
    (root/"af3_gui").write_text(code,encoding="utf8",newline="\n")
    os.chmod(root/"af3_gui",0o755)
    print("GUI built; isolated namespaces; %s bytes" % len(code.encode("utf8")))

if __name__=="__main__":main()
