#!/usr/bin/env python3
# ==============================================================================
# af3_qt_app.py - AF3 Desktop GUI (PySide6 / Qt, RELION-style X11 window)
# ==============================================================================
# 跑在登录/交互节点、当前用户账号下,窗口经 X11
# 回本机;sbatch 记在该用户名下。零隧道、即开即用。
#
# 线程模型:后台线程只做"跑逻辑",结果丢进 queue;主线程用 QTimer 轮询派发。
# 全程不在后台线程碰任何 GUI 对象 -> 无 "main loop" 类崩溃。
#
# 所有逻辑(拼命令/调 af3.py/读产物)都在 af3_ui_backend.py,本文件只渲染,
# 发布时由 merge_gui.py 把 UI 源码模块合并成
# 单文件 af3_gui；发布完整安装包以同步核心依赖。本文件仍是维护用的源文件,不要直接改 af3_gui。
#
# UI 支持中文与英文；字体随源码发布包提供。
# 视觉主题:双主题,顶栏右侧月亮/太阳钮切换并记住选择——
#   light = 暖奶油背景与珊瑚色按钮；
#   dark  = 近黑背景与珊瑚色按钮。
# 两套 token 见文件底部 _THEMES;品牌顶栏与每页 serif 标题在 App._build_header / _intro。
# ==============================================================================

import getpass
import html
import io
import json
import os
import re
import queue
import shutil
import socket
import sys
import threading
import time
from concurrent.futures import Future

# 防止 numpy/OpenBLAS 导入时按核心数狂开线程、在登录节点内存限制下分配失败
# ("OpenBLAS error: Memory allocation still failed")。GUI 不需要 BLAS 多线程,
# 必须在 import matplotlib/pandas(会拉入 numpy/OpenBLAS)之前设置。
for _v in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS",
           "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS"):
    os.environ.setdefault(_v, "1")

# 关键(six 冲突):PySide6 的 shiboken import-hook 会在之后任何一次
# "from six.moves import X" 时崩溃('_SixMetaPathImporter' has no '_path')。
# matplotlib/pandas 都要在后台线程懒加载(不拖启动),它们间接经 python-dateutil
# 拉 six.moves(dateutil.tz/rrule 里的 from six.moves import _thread, range)。
# 解法:在 import PySide6 之前,把 six.moves 和 dateutil 的相关子模块全部加载完
# (纯 Python、毫秒级);之后的懒加载全是 sys.modules 命中,不再触发冲突。
try:
    import six.moves  # noqa: F401
    import dateutil  # noqa: F401
    import dateutil.tz  # noqa: F401
    import dateutil.parser  # noqa: F401
    import dateutil.relativedelta  # noqa: F401
    import dateutil.rrule  # noqa: F401
    import dateutil.easter  # noqa: F401
except Exception:
    pass

# Defer plotting-library imports so network filesystems and font-cache creation
# do not block the first window. Results refresh once the imports are ready.
HAS_MPL = False
Figure = FigureCanvasAgg = plt = None

from PySide6 import QtCore, QtGui, QtWidgets
from shiboken6 import isValid

try:  # 图标字体(可选;纯 pip 包,字体随包自带,节点零依赖)
    import qtawesome as qta
except Exception:
    qta = None

import af3_ui_backend as B
import af3_ui_help as H
import af3_ui_widgets as UI

pd = None          # 后台线程懒加载(见 _load_heavy_libs),就绪前 Results 显示占位


def _load_heavy_libs():
    """后台线程:import matplotlib + pandas 并写回模块级名字。
    返回错误对象或 None。six.moves 已在 PySide6 之前预加载,此处迟导入安全。"""
    global HAS_MPL, pd, Figure, FigureCanvasAgg, plt
    err = None
    try:
        import matplotlib
        matplotlib.use("Agg")
        from matplotlib.figure import Figure as _Fig
        from matplotlib.backends.backend_agg import FigureCanvasAgg as _FCA
        import matplotlib.pyplot as _plt
        Figure, FigureCanvasAgg, plt = _Fig, _FCA, _plt
        HAS_MPL = True
    except Exception as e:
        err = e
    try:
        import pandas as _pd
        pd = _pd
    except Exception as e:
        err = e
    return err

USER = getpass.getuser()
HOST = socket.gethostname()
_MONO = None  # set in main()

# 每页 serif 大标题(ccDesign display 层级,正文说明仍走 af3_ui_help.PAGE_INTRO)
_PAGE_TITLES = {
    "setup": "Setup & deploy paths",
    "submit": "Submit a prediction",
    "pulldown": "Pulldown screen",
    "scan": "Scan screen",
    "dashboard": "Dashboard",
    "results": "Results",
    "help": "Help & guide",
}


class _ConsoleText(QtWidgets.QPlainTextEdit):
    """只读输出框:双击弹出大窗口查看全文(视觉换行,复制不含换行符)。"""

    def mouseDoubleClickEvent(self, ev):
        text = self.toPlainText()
        if text.strip():
            dlg = UI.QDialog(self)
            dlg.setAttribute(QtCore.Qt.WA_DeleteOnClose)
            UI.bind(dlg, 'setWindowTitle', UI.tr('Parse / command / output (double-click preview)'))
            dlg.resize(960, 620)
            v = QtWidgets.QVBoxLayout(dlg)
            te = UI.QPlainTextEdit()
            te.setReadOnly(True)
            te.setLineWrapMode(QtWidgets.QPlainTextEdit.WidgetWidth)
            te.setFont(self.font())
            UI.bind(te, 'setPlainText', text)
            v.addWidget(te, 1)
            brow = QtWidgets.QHBoxLayout()
            bcp = UI.QPushButton(UI.tr('Copy all'))
            bcp.clicked.connect(
                lambda: UI.bind(QtGui.QGuiApplication.clipboard(), 'setText', text))
            brow.addWidget(bcp)
            brow.addStretch(1)
            bcl = UI.QPushButton(UI.tr('Close'))
            bcl.clicked.connect(dlg.close)
            brow.addWidget(bcl)
            v.addLayout(brow)
            dlg.show()
        super().mouseDoubleClickEvent(ev)


class HeatmapLabel(QtWidgets.QLabel):
    """Agg 图 + 窗口缩放自适应 + (matrix 时)悬停 tooltip。
    不用 backend_qtagg(坑 #2):PNG 照出,QLabel 上按分数坐标反算格子。
    axes_frac = 热图区(ax.get_position())在整张图里的分数矩形 (x0,y0,w,h);
    因为缩放保比例,分数矩形与显示尺寸无关,悬停换算始终精确。"""

    def __init__(self, pm_orig, values=None, row_labels=None, col_labels=None,
                 axes_frac=None, rank_info=None, names=None,
                 row_name="A", col_name="B", val_name="ipTM", val_fmt="{:.3f}",
                 chains=None, ds=1):
        super().__init__()
        self._orig = pm_orig
        self._pm = pm_orig
        self._vals = values          # None -> 纯图片(profile 图),无悬停
        self._rows = row_labels or []
        self._cols = col_labels or []
        self._af = axes_frac
        self._rank = rank_info or {}
        self._names = names or {}    # accession -> entry 名(#5 标注)
        self._rn, self._cn = row_name, col_name
        self._vn, self._vf = val_name, val_fmt
        self._chains = chains        # PAE 链边界 [{"label","start","end"}]
        self._ds = max(1, ds)        # PAE 降采样步长(坐标换算回原始残基号)
        self.setAlignment(QtCore.Qt.AlignHCenter | QtCore.Qt.AlignTop)
        self.setMinimumSize(360, 240)
        if self._vals is not None:
            self.setMouseTracking(True)
        # (#3)缩放防抖:拖动期间不重采样,松手 80ms 后才平滑重绘(省集群 CPU/传输)
        self._fit_timer = QtCore.QTimer(self)
        self._fit_timer.setSingleShot(True)
        self._fit_timer.setInterval(80)
        self._fit_timer.timeout.connect(self._fit_now)
        self._fit_now()

    def _fit_now(self):
        if self._orig is None or self._orig.isNull():
            return
        w = max(360, self.width() - 4)
        h = max(240, self.height() - 4)
        self._pm = self._orig.scaled(w, h, QtCore.Qt.KeepAspectRatio,
                                     QtCore.Qt.SmoothTransformation)
        self.setPixmap(self._pm)

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self._fit_timer.start()

    def _anno(self, label):
        acc, _ = B.split_acc_label(label)
        nm = self._names.get(acc) if acc else None
        return f"{label} ({nm})" if nm else label

    def _chain_label(self, i):
        """PAE:降采样格子序号 -> '链标签: 链内原始残基序号'(带 entry 名标注)。"""
        o = i * self._ds            # 还原为原始残基 index(0-based)
        for ch in self._chains:
            if ch["start"] <= o < ch["end"]:
                return f"{self._anno(ch['label'])}: {o - ch['start'] + 1}"
        return str(o + 1)

    def mouseMoveEvent(self, ev):
        pm = self._pm
        if (self._vals is not None and self._af is not None
                and pm is not None and not pm.isNull() and pm.width() > 0):
            px = ev.position().x() - max(0, (self.width() - pm.width()) // 2)
            py = ev.position().y()
            x0, y0, w, h = self._af
            fx = px / pm.width()
            fy = 1 - py / pm.height()          # 图分数坐标(y 转为自底部向上)
            cf = (fx - x0) / w                 # 热图区内横向分数
            rf = 1 - (fy - y0) / h             # 区内纵向分数(行自上而下)
            ncol, nrow = len(self._cols), len(self._rows)
            c, r = int(cf * ncol), int(rf * nrow)
            if 0 <= cf <= 1 and 0 <= rf <= 1 and 0 <= r < nrow and 0 <= c < ncol:
                if self._chains:
                    ra, cb = self._chain_label(r), self._chain_label(c)
                else:
                    ra, cb = self._anno(self._rows[r]), self._anno(self._cols[c])
                try:
                    vs = self._vf.format(float(self._vals[r][c]))
                except Exception:
                    vs = str(self._vals[r][c])
                lines = [(f"{self._rn}: {ra}" if self._rn else ra),
                         (f"{self._cn}: {cb}" if self._cn else cb),
                         f"{self._vn}: {vs}"]
                info = self._rank.get((ra, cb)) or {}
                if info.get("rank"):
                    lines.append(f"rank: {info['rank']}")
                QtWidgets.QToolTip.showText(ev.globalPosition().toPoint(),
                                            "\n".join(lines), self)
                return super().mouseMoveEvent(ev)
        QtWidgets.QToolTip.hideText()
        super().mouseMoveEvent(ev)

    def leaveEvent(self, ev):
        QtWidgets.QToolTip.hideText()
        super().leaveEvent(ev)


class BarHoverLabel(HeatmapLabel):
    """(#5)1D 条形图悬停:x 方向映射到第几根 bar,显示 frag/best ipTM/best partner。"""

    def __init__(self, pm_orig, items, axes_frac):
        super().__init__(pm_orig)          # values=None:只要缩放框架,不要矩阵悬停
        self._items = items                # [{frag, best_iptm, best_partner}]
        self.setMouseTracking(True)

    def mouseMoveEvent(self, ev):
        pm = self._pm
        handled = False
        if (pm is not None and not pm.isNull() and pm.width() > 0
                and self._af is not None):
            px = ev.position().x() - max(0, (self.width() - pm.width()) // 2)
            py = ev.position().y()
            x0, y0, w, h = self._af
            fx = px / pm.width()
            fy = 1 - py / pm.height()
            cf = (fx - x0) / w
            rf = (fy - y0) / h
            n = len(self._items)
            c = int(cf * n)
            if 0 <= cf <= 1 and 0 <= rf <= 1 and 0 <= c < n:
                it = self._items[c]
                lines = [f"frag: {it['frag']}",
                         f"best ipTM: {it['best_iptm']:.3f}"]
                if it.get("best_partner"):
                    lines.append(f"best partner: {it['best_partner']}")
                QtWidgets.QToolTip.showText(ev.globalPosition().toPoint(),
                                            "\n".join(lines), self)
                handled = True
        if not handled:
            QtWidgets.QToolTip.hideText()
        QtWidgets.QLabel.mouseMoveEvent(self, ev)


# ----------------------------------------------------------------------------
# 后台线程:只做逻辑;结果进 queue,主线程 _drain 派发。线程内不碰 GUI。
# ----------------------------------------------------------------------------
class _DaemonPool:
    def __init__(self, workers=4):
        self.jobs=queue.Queue()
        for i in range(workers):
            thread=threading.Thread(target=self._run,name="af3-ui-%s" % i,daemon=True);thread.start()
    def _run(self):
        while True:
            fn,future=self.jobs.get()
            if future.set_running_or_notify_cancel():
                try: future.set_result(fn())
                except BaseException as exc: future.set_exception(exc)
            self.jobs.task_done()
    def submit(self,fn):
        future=Future();self.jobs.put((fn,future));return future
_WORKERS = _DaemonPool()
_PAE_WORKERS = _DaemonPool(workers=2)
_PENDING = threading.BoundedSemaphore(16)

def enqueue(q, target, on_done, pool=None):
    if not _PENDING.acquire(blocking=False):
        q.put((on_done, RuntimeError("后台请求较多，请稍后重试"), None)); return None
    def run():
        try: q.put((on_done, None, target()))
        except Exception as exc: q.put((on_done, exc, None))
        except SystemExit as exc: q.put((on_done, RuntimeError('Background command exited: ' + str(exc)), None))
        finally: _PENDING.release()
    future = (pool or _WORKERS).submit(run)
    def cancelled(f):
        if f.cancelled():
            _PENDING.release();q.put((on_done,B.AnalysisCancelled('Cancelled'),None))
    future.add_done_callback(cancelled)
    return future


class _ScreenTab:
    """(#6)Pulldown / Scan 各自的独立页面——同一张表单两套实例,防误选模式。
    逻辑(拼命令/组合数/切片预览)全在 backend,这里只渲染 + 收集输入。"""

    def __init__(self, app, mode):
        self.app = app
        self.mode = mode                       # 'pulldown' | 'scan'
        w = self.w = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(w)
        lay.addLayout(app._intro(mode))
        # (#17)--name 提到标题正下方,命名提示紧随其后(三页布局一致)
        nrow = QtWidgets.QHBoxLayout()
        nrow.addWidget(UI.QLabel(UI.tr('--name')))
        self.name = UI.QLineEdit()
        UI.bind(self.name, 'setPlaceholderText', UI.tr('auto (e.g. P12345x13_vs_Q12345x4)'))
        self.name.setMinimumWidth(360)     # 原宽度的 ~3 倍,长批次名看得全
        nrow.addWidget(self.name, 1)
        nrow.addWidget(app._help_button("name"))
        lay.addLayout(nrow)
        lay.addWidget(app._hint_label("screen_autoname"))
        outer = lay
        vertical = QtWidgets.QVBoxLayout(); vertical.setContentsMargins(0,0,0,0)
        outer.addLayout(vertical, 1)
        form = QtWidgets.QWidget(); lay = QtWidgets.QVBoxLayout(form)
        lay.setContentsMargins(0, 0, 0, 0)
        mid = UI.ResponsiveColumns(threshold=900)
        for tag in ("a", "b"):
            gb = QtWidgets.QWidget()
            gl = QtWidgets.QVBoxLayout(gb); gl.setContentsMargins(0,0,0,0); gl.setSpacing(4)
            gl.addWidget(UI.QLabel(UI.f("Group {0} · one expression per line", tag.upper())))
            te = UI.QPlainTextEdit()
            app._reg_mono(te)
            te.setMinimumHeight(80); te.setMaximumHeight(120)
            UI.bind(te, 'setPlaceholderText', UI.tr('P12345\nQ12345+P12345'))
            gl.addWidget(te)
            r = QtWidgets.QHBoxLayout()
            lb = UI.QPushButton(UI.f("Load {0} file…", tag.upper()))
            lb.setStyleSheet('padding: 2px 9px;')
            lb.clicked.connect(lambda _=False, t=te: self._load(t))
            r.addWidget(lb); r.addWidget(app._help_button("screen_ab")); r.addStretch(1)
            gl.addLayout(r)
            mid.addPanel(gb, 1)
            count = UI.QLabel(); count.setProperty("caption", True); r.addWidget(count)
            te.textChanged.connect(lambda t=te,c=count: UI.bind(c, 'setText', UI.f("{0} input lines", sum(bool(x.strip()) and not x.lstrip().startswith("#") for x in t.toPlainText().splitlines()))))
            UI.bind(count, 'setText', UI.f("{0} input lines", 0))
            setattr(self, tag, te)
        lay.addWidget(mid)

        if mode == "scan":
            sgb = QtWidgets.QWidget()
            v = QtWidgets.QVBoxLayout(sgb); v.setContentsMargins(0,0,0,0); v.setSpacing(4)
            top = QtWidgets.QHBoxLayout()
            top.addWidget(UI.QLabel(UI.tr('--mode')))
            self.mode_win = UI.QRadioButton(UI.tr('win — fixed sliding window'))
            self.mode_pae = UI.QRadioButton(UI.tr('pae — PAE domain windows'))
            self.mode_win.setChecked(True)
            top.addWidget(self.mode_win)
            top.addWidget(self.mode_pae)
            top.addWidget(app._help_button("scan_mode"))
            top.addStretch(1)
            v.addLayout(top)

            # win 模式参数行
            self.win_box = QtWidgets.QWidget()
            g = QtWidgets.QGridLayout(self.win_box)
            g.setContentsMargins(0, 0, 0, 0)
            g.setHorizontalSpacing(6); g.setVerticalSpacing(4)
            for col in (3,7):g.setColumnMinimumWidth(col,44)
            g.setColumnStretch(11,1)
            self.scan_params_grid = g
            self._win_controls = []
            self.win = app._spin(50, 5000, 300); self.ov = app._spin(0, 2000, 100)
            self.mf = app._spin(10, 2000, 100)
            # 组内 label-输入框-? 紧贴,组间统一间距——不再把 ? 甩到下一个参数旁边
            for col, (lab, sp, key) in enumerate((("--win", self.win, "win"),
                                 ("--overlap", self.ov, "overlap"),
                                 ("--min-frag", self.mf, "min_frag"))):
                label=UI.QLabel(lab); help_button=app._help_button(key)
                sp.setMinimumWidth(84)
                g.addWidget(label,0,col*4);g.addWidget(sp,0,col*4+1);g.addWidget(help_button,0,col*4+2)
                self._win_controls.extend((label,sp,help_button))
            v.addWidget(self.win_box)

            # pae 模式参数(默认隐藏;QGridLayout 两行三列,列宽自动对齐)
            self.pae_box = QtWidgets.QWidget()
            g2 = QtWidgets.QGridLayout(self.pae_box)
            g2.setContentsMargins(0, 0, 0, 0)
            g2.setHorizontalSpacing(6)
            g2.setVerticalSpacing(4)
            self.pae_cut = UI.QDoubleSpinBox()
            self.pae_cut.setRange(0.5, 30.0); self.pae_cut.setSingleStep(0.5)
            self.pae_cut.setValue(5.0); self.pae_cut.setDecimals(1)
            self.pae_res = UI.QDoubleSpinBox()
            self.pae_res.setRange(0.05, 4.0); self.pae_res.setSingleStep(0.1)
            self.pae_res.setValue(0.5); self.pae_res.setDecimals(2)
            self.pae_mdom = app._spin(2, 200, 10)
            self.pae_dpw = app._spin(1, 10, 1)
            self.pae_mf = app._spin(10, 2000, 250)
            self.pae_maxf = app._spin(0, 20000, 0)
            UI.bind(self.pae_maxf, 'setSpecialValueText', UI.tr('auto (=threshold)'))
            pae_items = (
                ("--pae-cutoff", self.pae_cut, "pae_cutoff"),
                ("--pae-resolution", self.pae_res, "pae_resolution"),
                ("--pae-min-domain", self.pae_mdom, "pae_min_domain"),
                ("--pae-domains-per-window", self.pae_dpw,
                 "pae_domains_per_window"),
                ("--pae-min-frag", self.pae_mf, "pae_min_frag"),
                ("--pae-max-frag", self.pae_maxf, "pae_max_frag"),
            )
            for i, (lab, sp, key) in enumerate(pae_items):
                r, c = divmod(i, 3)
                sp.setMinimumWidth(84)
                g2.addWidget(UI.QLabel(lab), r, c * 4)
                g2.addWidget(sp, r, c * 4 + 1)
                g2.addWidget(app._help_button(key), r, c * 4 + 2)
            for col in (3,7):g2.setColumnMinimumWidth(col,44)
            g2.setColumnStretch(11, 1)
            self.pae_box.setVisible(False)
            v.addWidget(self.pae_box)

            self.mode_win.toggled.connect(self._on_scan_mode)
            lay.addWidget(sgb)

        self.topk = app._spin(0, 100000, 0)
        self.self_cb = UI.QCheckBox(UI.tr('--self') if mode != 'scan' else '')
        if mode == 'scan':
            self.th = app._spin(100, 20000, 500)
            for col,(lab,control,key) in enumerate((('--split-threshold',self.th,'threshold'),
                    ('--topk (0=all)',self.topk,'topk'),('--self',self.self_cb,'self_pairs'))):
                label=UI.QLabel(UI.tr(lab));label.setBuddy(control)
                UI.bind(control,'setAccessibleName',UI.tr(lab))
                if control is not self.self_cb:control.setMinimumWidth(84)
                g.addWidget(label,1,col*4);g.addWidget(control,1,col*4+1);g.addWidget(app._help_button(key),1,col*4+2)
        else:
            opt = QtWidgets.QHBoxLayout()
            opt.addWidget(UI.QLabel(UI.tr('--topk (0=all)')));opt.addWidget(self.topk)
            opt.addWidget(app._help_button('topk'));opt.addWidget(self.self_cb)
            opt.addWidget(app._help_button('self_pairs'));opt.addStretch(1);lay.addLayout(opt)
        gb, self.opts = app._adv_options(scan_mode=(mode == "scan"))
        lay.addWidget(gb)
        form_container = QtWidgets.QWidget(); sticky = QtWidgets.QVBoxLayout(form_container)
        sticky.setContentsMargins(0, 0, 0, 0)
        sticky.addWidget(app._wrap_scroll(form), 1)

        btns = QtWidgets.QHBoxLayout()
        self.frag = UI.QPushButton(UI.tr('Fetch seq + fragment preview (online)'))
        self.dry = UI.QPushButton(UI.tr('Preview plan'))
        self.go = UI.QPushButton(UI.tr('Submit')); self.go.setDefault(True)
        self.go.setObjectName("primary")
        if qta:
            self.frag.setIcon(app._ic("fa5s.search"))
            self.dry.setIcon(app._ic("fa5s.eye"))
            self.go.setIcon(app._ic("fa5s.paper-plane", "#ffffff"))
        self.frag.clicked.connect(self._frag_preview)
        self.dry.clicked.connect(lambda: self._run(True))
        self.go.clicked.connect(lambda: self._run(False))
        for b in (self.frag, self.dry, self.go):
            btns.addWidget(b)
        btns.addStretch(1)
        sticky.addLayout(btns)

        self.metrics = UI.QLabel(UI.tr('A —   B —   pairs —   models —'))
        f = self.metrics.font(); f.setPointSize(f.pointSize() + 2); f.setBold(True)
        self.metrics.setFont(f)
        self.metrics.setStyleSheet(f"color:{CC['primary']}")
        sticky.addWidget(self.metrics)
        vertical.addWidget(form_container, 1)
        self.out = app._out(); self.output_panel = UI.OutputPanel(self.out, always_open=True)
        vertical.addWidget(self.output_panel)
        # 防抖:停笔 0.4s 才重算组合数(热路径不读网络盘,坑 #6)
        self._mtimer = QtCore.QTimer(w)
        self._mtimer.setSingleShot(True); self._mtimer.setInterval(400)
        self._mtimer.timeout.connect(self.update_metrics)
        self.a.textChanged.connect(self._mtimer.start)
        self.b.textChanged.connect(self._mtimer.start)
        if mode == "scan":   # (#5)切分参数变化也要重算片段数与组合数
            for sp in (self.win, self.ov, self.mf, self.th,
                       self.pae_cut, self.pae_res, self.pae_mdom,
                       self.pae_dpw, self.pae_mf, self.pae_maxf):
                sp.valueChanged.connect(self._mtimer.start)
        QtCore.QTimer.singleShot(100, self.update_metrics)

    def _scan_mode(self):
        return "pae" if self.mode_pae.isChecked() else "win"

    def _on_scan_mode(self, *_):
        is_win = self._scan_mode() == "win"
        for control in self._win_controls:control.setVisible(is_win)
        self.pae_box.setVisible(not is_win)
        self._mtimer.start()

    def _load(self, widget):
        p, _ = QtWidgets.QFileDialog.getOpenFileName(
            self.app, UI.render(UI.tr("Load list")), "", UI.render(UI.tr("Text files (*.txt *.tsv *.csv);;All files (*)")))
        if p:
            try:
                UI.bind(widget, 'setPlainText', B.read_text(p)); self.update_metrics()
            except Exception as e:
                UI.QMessageBox.critical(self.app, UI.tr('Read failed'), str(e))

    @staticmethod
    def _parse_text(text, fetch):
        """每行解析为一个"侧"(1+ 个实体:蛋白/DNA/RNA/配体,+ 连接,允许 xN);
        返回 [(ln, [ent...])]。"""
        out = []
        for ln in B.split_lines(text):
            ents, err = B.capture_af3_parse(ln, fetch=fetch)
            if not err and ents:
                out.append((ln, ents))
        return out

    def update_metrics(self):
        token = getattr(self,"_metrics_generation",0)+1;self._metrics_generation=token
        if self.a.document().blockCount()+self.b.document().blockCount()>1000:
            UI.bind(self.metrics,'setText',UI.tr('大列表请点击 Preview plan 获取精确计划'));return
        a=self._parse_text(self.a.toPlainText(),fetch=False);b=self._parse_text(self.b.toPlainText(),fetch=False)
        params={}
        if self.mode=="scan":
            params=dict(mode=self._scan_mode(),win=self.win.value(),overlap=self.ov.value(),min_frag=self.mf.value(),threshold=self.th.value())
            if params["mode"]=="win" and params["overlap"]>=params["win"]:
                UI.bind(self.metrics, 'setText', UI.tr('参数错误：overlap 必须小于 win'))
                self.go.setEnabled(False);self.dry.setEnabled(False);self.frag.setEnabled(False);return
        self.go.setEnabled(True);self.dry.setEnabled(True);self.frag.setEnabled(True)
        if not a or not b:
            UI.bind(self.metrics, 'setText', UI.tr('填写 A/B 输入后显示任务计划'));return
        if len(a)+len(b)>1000:
            UI.bind(self.metrics, 'setText', UI.tr('大列表请点击 Preview plan 获取精确计划'));return
        options=self.opts();self_pairs=self.self_cb.isChecked()
        UI.bind(self.metrics, 'setText', UI.tr('正在计算任务计划…'))
        def done(err,result):
            if token!=self._metrics_generation:return
            if err:UI.bind(self.metrics, 'setText', UI.f("{0}{1}", UI.tr('计划无效: '), str(err)));return
            if result is None:UI.bind(self.metrics, 'setText', UI.tr('需读取序列或 PAE；点击 Preview plan 查看精确计划'));return
            UI.bind(self.metrics, 'setText', UI.percent('A %s   B %s   tasks %s   seeds/task %s   samples %s', (result["a"],result["b"],result["pairs"],result["seeds"],result["models"])))
        enqueue(self.app._q,lambda:B.preview_screen_plan([es for _,es in a],[es for _,es in b],self.mode,params,options,self_pairs),done)

    def _frag_preview(self):
        app = self.app
        UI.bind(self.out, 'setPlainText', UI.tr('Fetching sequences / fragmenting…'))
        app._feedback(self.out, UI.tr('Fetching sequences / fragmenting…'), UI.tr('No prediction submitted.'), UI.tr('Sequence retrieval can take a while.'))
        app._busy([self.frag, self.dry, self.go], True)
        # 线程规矩(坑 #1):文本与参数先在 GUI 线程取好,worker 只碰数据不碰控件
        a_text, b_text = self.a.toPlainText(), self.b.toPlainText()
        pae_mode = (self.mode == "scan"
                    and self._scan_mode() == "pae")
        params = ((self.win.value(), self.ov.value(), self.mf.value(), self.th.value())
                  if self.mode == "scan" else (300, 100, 100, 500))
        pae_params = {"threshold": self.th.value(),
                      "pae_cutoff": self.pae_cut.value(),
                      "resolution": self.pae_res.value(),
                      "min_domain": self.pae_mdom.value(),
                      "domains_per_window": self.pae_dpw.value(),
                      "min_window": self.pae_mf.value(),
                      "max_span": self.pae_maxf.value() or None} if pae_mode else None

        def target():
            a = _ScreenTab._parse_text(a_text, fetch=True)
            b = _ScreenTab._parse_text(b_text, fetch=True)
            flat_a = [e for _, es in a for e in es]
            flat_b = [e for _, es in b for e in es]
            B.resolve_msa_keys(flat_a); B.resolve_msa_keys(flat_b)
            items = [("A", e) for e in flat_a] + [("B", e) for e in flat_b]
            if pae_mode:
                rows, _ = B.scan_fragment_preview_pae(items, pae_params)
            else:
                rows, _ = B.scan_fragment_preview(items, *params)
            return a, b, rows

        def done(err, res):
            app._busy([self.frag, self.dry, self.go], False)
            UI.bind(self.out, 'setPlainText', UI.tr(''))
            if err:
                self.out.appendPlainText(f"Failed: {err}")
                app._feedback(self.out, UI.tr('Preview failed.'), str(err), UI.tr('No prediction submitted.'), UI.tr('Open details, fix the reported issue, then retry.')); return
            a, b, rows = res
            nfa = sum(1 for r in rows if r["group"] == "A")
            nfb = sum(1 for r in rows if r["group"] == "B")
            self.out.appendPlainText(
                f"Fragments: A {len(a)} proteins -> {nfa} frags; B {len(b)} proteins -> {nfb} frags\n")
            for r in rows:
                dom = f"  [{r['domains']}]" if r.get("domains") else ""
                self.out.appendPlainText(
                    f"  [{r['group']}] {r['label']:<14} {r['frag']:<10} "
                    f"len={r['length']}{dom}")
            app._feedback(self.out, UI.tr('Fragment preview complete.'),
                          UI.f('A: {0} inputs → {1} fragments; B: {2} inputs → {3} fragments', len(a),nfa,len(b),nfb),
                          UI.tr('No prediction submitted.'), UI.tr('Review fragment details, then preview the execution plan.'))
        enqueue(app._q, target, done)

    def _run(self, dry):
        app = self.app
        a_lines = B.split_lines(self.a.toPlainText())
        b_lines = B.split_lines(self.b.toPlainText())
        sp = None
        if self.mode == "scan":
            sp = {"mode": self._scan_mode(),
                  "win": self.win.value(), "overlap": self.ov.value(),
                  "min_frag": self.mf.value(), "threshold": self.th.value(),
                  "pae_cutoff": self.pae_cut.value(),
                  "pae_resolution": self.pae_res.value(),
                  "pae_min_domain": self.pae_mdom.value(),
                  "pae_domains_per_window": self.pae_dpw.value(),
                  "pae_min_frag": self.pae_mf.value(),
                  "pae_max_frag": self.pae_maxf.value()}
        # (#3)空名字 -> 自动带上 A/B 首个 UniProt id,Dashboard 一眼认任务
        name = self.name.text().strip() or B.suggest_screen_name(a_lines, b_lines)
        args, err = B.build_screen_args(
            self.mode, a_lines, b_lines,
            name=name, topk=self.topk.value(),
            self_pairs=self.self_cb.isChecked(), scan_params=sp,
            opts=self.opts(), dry_run=dry, user_tag="")
        if err:
            UI.bind(self.out, 'setPlainText', str(err))
            app._feedback(self.out, UI.tr('Invalid input'), str(err), UI.tr('No prediction submitted.'))
            UI.QMessageBox.critical(app, UI.tr('Invalid input'), err); return
        app._exec(args, dry, [self.frag, self.dry, self.go], self.out)


class App(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        global qta
        if qta:
            try:qta.icon("fa5s.check")
            except Exception:qta=None
        UI.bind(self, 'setWindowTitle', UI.f('AF3 UI  [{0}@{1}]: {2}', USER, HOST, B.HOST_BASE))
        self.resize(1300, 860)
        self._q = queue.Queue()
        self._closing = False
        self._msa_sync_busy = False
        self._msa_startup_checked = False
        self._timer = QtCore.QTimer(self)
        self._timer.timeout.connect(self._drain)
        self._timer.start(80)

        # Per-user appearance preferences, separate from deployment settings.
        self._settings = QtCore.QSettings("af3_console", "public")
        # The packaged interpreter and core are pinned; data settings are independent.
        self._lang = str(self._settings.value("lang", "zh"))
        UI.set_language(self._lang)
        self._font_size = int(self._settings.value("fontSize", 11))
        self._theme = str(self._settings.value("theme", "light"))
        if self._theme not in _THEMES: self._theme = 'light'
        self._mono_widgets = []     # 字号变化时要重设等宽字体的控件
        self._intro_labels = []     # 每页顶部说明(语言切换时刷新)
        self._hint_labels = []      # 页面内零散描述句(双语,随 中/EN 刷新)
        self._pae_token = 0
        self._pae_last = None
        self._render_generation = 0
        self._render_key = None
        self._dash_inflight = self._res_inflight = False
        self._detail_generation = 0
        self._names = {}            # 当前 Results 批次的 accession -> entry 名
        self._names_refetched = set()
        self._html_done = set()     # 本会话已自动生成过 HTML 报告的批次

        self._status_home()
        # Brand bar: embedded pixel cat, wordmark and display preferences.
        central = QtWidgets.QWidget()
        cv = QtWidgets.QVBoxLayout(central)
        cv.setContentsMargins(0, 0, 0, 0); cv.setSpacing(0)
        cv.addWidget(self._build_header())
        tabs = QtWidgets.QTabWidget()
        cv.addWidget(tabs, 1)
        self.setCentralWidget(central)
        self.tabs = tabs
        self._build_setup()
        self._build_submit()
        self.pd = _ScreenTab(self, "pulldown")
        self.sc = _ScreenTab(self, "scan")
        self._pd_tab = self.pd.w
        self._sc_tab = self.sc.w
        UI.tab(self.tabs, self._pd_tab, UI.tr('Pulldown'))
        UI.tab(self.tabs, self._sc_tab, UI.tr('Scan'))
        # 兼容别名(测试与旧调用)
        self.scr_a, self.scr_b = self.pd.a, self.pd.b
        self.scr_name, self.scr_metrics = self.pd.name, self.pd.metrics
        self._build_dashboard()
        self._build_results()
        self._build_help()
        if qta:  # 标签页图标(可选装饰,没有 qtawesome 也不影响功能)
            for _i, _n in enumerate(["fa5s.wrench", "fa5s.rocket", "fa5s.project-diagram",
                                     "fa5s.cut", "fa5s.tachometer-alt", "fa5s.chart-bar",
                                     "fa5s.question-circle"]):
                self.tabs.setTabIcon(_i, qta.icon(_n, color=CC["muted"]))
        # (#8)字号规则始终生效(含默认值),因为它还负责压住主题写死的字号
        self._set_font_size(self._font_size, quiet=True)
        self._selectable_labels(self)
        # 重库(matplotlib/pandas)后台懒加载:就绪后重渲 Results,不拖启动
        enqueue(self._q, _load_heavy_libs, self._libs_done)
        initial = int(self._settings.value("last_tab", 1 if os.path.isfile(B.R.config_path()) else 0))
        self.tabs.setCurrentIndex(min(max(initial,0),self.tabs.count()-1))
        self.tabs.currentChanged.connect(lambda i:self._settings.setValue("last_tab",i))
        self._set_lang(self._lang)
        # One read-only scan per window launch; never scan during page/theme refresh.
        self._msa_startup_roots = tuple(B.msa_pool_paths())
        QtCore.QTimer.singleShot(0, self._startup_msa_sync)

    def _libs_done(self, err, res):
        if res is not None:
            self.statusBar().showMessage(UI.render(UI.f('plot/table libs failed to load: {0}', res)), 8000)
        if getattr(self, "_res_batches", None) is not None:
            self._res_render()      # 库就绪:把 Results 里的 loading 占位换成真内容

    def _scr_metrics(self):  # 兼容旧测试入口
        self.pd.update_metrics()

    # ---- queue drain (GUI thread) ----
    def _drain(self):
        until = time.monotonic() + .012
        while time.monotonic() < until:
            try: cb, err, result = self._q.get_nowait()
            except queue.Empty: break
            try: cb(err,result)
            except Exception as exc:
                self.statusBar().showMessage(UI.render(UI.f("{0}{1}", UI.tr('界面更新失败: '), str(exc))), 10000)
                print("GUI callback error:",exc,file=sys.stderr)

    # ---- event filter:各选项卡条屏蔽滚轮切换(滚动内容时易误触换页/换类型)----
    def eventFilter(self, obj, ev):
        if (ev.type() == QtCore.QEvent.Wheel
                and isinstance(obj, QtWidgets.QTabBar)):
            return True
        return super().eventFilter(obj, ev)

    # ---- small helpers ----
    def _reg_mono(self, w):
        """登记使用等宽字体的控件:字号变化(A-/A+)时统一重设。"""
        if _MONO:
            w.setFont(_MONO)
        self._mono_widgets.append(w)
        return w

    def _mono_text(self, readonly=True):
        w = UI.QPlainTextEdit()
        w.setReadOnly(readonly)
        if readonly:
            # 只读输出 = 浅奶油输出卡(与可编辑输入的 canvas 白底区分)
            w.setObjectName("console")
            # 输出是命令行/日志/ASCII 树:不折行,长了横滚——折行会把字符叠在一起
            w.setLineWrapMode(QtWidgets.QPlainTextEdit.NoWrap)
        self._reg_mono(w)
        return w

    def _out(self):
        """Read-only detail log; submission pages expose a separate four-line summary."""
        w = _ConsoleText()
        w.setReadOnly(True)
        w.setObjectName("console")
        w.setLineWrapMode(QtWidgets.QPlainTextEdit.WidgetWidth)
        w.setMinimumHeight(200)
        self._reg_mono(w)
        return w

    def _show_result(self, w, res, cmd=None):
        if cmd:
            w.appendPlainText("$ " + cmd)
        for kind, txt in res["blocks"]:
            w.appendPlainText(txt)
        sb = w.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _busy(self, widgets, on):
        for w in widgets:
            w.setEnabled(not on)

    # ---- 图标 / 帮助 / 剪贴板 小助手 ----
    def _ic(self, name, color="#444444"):
        return qta.icon(name, color=color) if qta else QtGui.QIcon()

    def _help_button(self, key):
        """参数旁的 "?" 小圆钮(RELION 式):点开该参数的说明。文案在 af3_ui_help.py。"""
        b = UI.QToolButton()
        b.setAutoRaise(True)
        b.setProperty("help", True)      # ccDesign:参数旁小圆钮无边框、弱灰色
        b.setCursor(QtCore.Qt.PointingHandCursor)
        if qta:
            b.setIcon(qta.icon("fa5s.question-circle", color=CC["muted_soft"]))
        else:
            UI.bind(b, 'setText', UI.tr('?'))
        UI.bind(b, 'setToolTip', UI.tr('What is this?'))
        b.clicked.connect(lambda _=False, k=key: self._show_help(k))
        return b

    def _show_help(self, key):
        self._help_key = key
        title, body = H.parameter_html(key, self._lang, CC)
        dlg = getattr(self, "_help_dlg", None)
        if dlg is None:
            dlg = UI.QDialog(self); dlg.setWindowFlag(QtCore.Qt.Window)
            box = QtWidgets.QVBoxLayout(dlg)
            dlg._body = UI.ReadableBrowser(); dlg._body.setOpenExternalLinks(False); dlg._body.anchorClicked.connect(self._help_link)
            box.addWidget(dlg._body, 1)
            row = QtWidgets.QHBoxLayout(); row.addStretch()
            close = UI.QPushButton(UI.tr('Close')); close.clicked.connect(dlg.close); row.addWidget(close); box.addLayout(row)
            self._help_dlg = dlg
        UI.bind(dlg, 'setWindowTitle', title); dlg._body.setHtml(body)
        if getattr(dlg, '_sized_key', None) != key:
            screen = self.screen().availableGeometry()
            width = min(820, screen.width()-80)
            measure = dlg._body.document().clone()
            measure.setTextWidth(width-90)
            height = min(screen.height()-80, 680, max(230, int(measure.size().height())+95))
            dlg.resize(width, height)
            dlg._sized_key = key
            measure.deleteLater()
        dlg.show(); dlg.raise_(); dlg.activateWindow()

    def _label_with_help(self, text, key):
        row = QtWidgets.QHBoxLayout()
        row.addWidget(UI.QLabel(text))
        row.addWidget(self._help_button(key))
        row.addStretch(1)
        return row

    def _copy(self, text, what="Path"):
        """X11 会把剪贴板同步到 MobaXterm 本机 -> 复制路径=本地 Ctrl+V 可得。"""
        UI.bind(QtGui.QGuiApplication.clipboard(), 'setText', text)
        self.statusBar().showMessage(UI.render(UI.f('{0} copied — paste locally (Ctrl+V) or into MobaXterm SFTP panel: {1}', what, text)), 6000)

    def _copy_file_content(self, path):
        try:
            if os.path.getsize(path) > 2_000_000:
                UI.QMessageBox.warning(self, UI.tr('Too large'), UI.tr('File > 2 MB; please download it instead.'))
                return
            self._copy(B.read_text(path), "File content")
        except Exception as e:
            UI.QMessageBox.critical(self, UI.tr('Copy failed'), str(e))

    def _dl_row(self, path, dl):
        """#1:不再提供 Download(界面在集群上,只能存集群,没意义)。
        改为一键复制**所在文件夹**路径(MobaXterm SFTP 地址栏只接受文件夹),
        小文本另给 Copy content(内容直接进本地剪贴板)。"""
        row = QtWidgets.QHBoxLayout()
        b = UI.QPushButton(UI.tr('Copy folder path'))
        UI.bind(b, 'setToolTip', UI.tr('Copy the CONTAINING FOLDER path (paste into MobaXterm SFTP panel address bar)'))
        b.clicked.connect(lambda: self._copy(os.path.dirname(path), "Folder path"))
        row.addWidget(b)
        bc = UI.QPushButton(UI.tr('Copy content'))
        UI.bind(bc, 'setToolTip', UI.tr('Copy file content to clipboard (paste into a local editor)'))
        bc.clicked.connect(lambda: self._copy_file_content(path)); row.addWidget(bc)
        row.addStretch(1)
        return row

    # ---- ccDesign 品牌顶栏(top-nav)+ 每页 serif 标题 ----
    def _build_header(self):
        """奶油顶栏:用户提供的猫图 + 'AF3 Console' 字标 + 用户@主机说明,
        右侧 A-/A+ 与 中/EN 芯片(从原标签栏角落搬来,更像产品顶导航)。"""
        bar = QtWidgets.QWidget(); bar.setObjectName("brandbar")
        h = QtWidgets.QHBoxLayout(bar)
        h.setContentsMargins(18, 10, 14, 8); h.setSpacing(8)
        self.brand_cat = UI.BrandCat()
        h.addWidget(self.brand_cat)
        word = UI.QLabel(UI.tr('AF3 Console')); word.setObjectName("wordmark")
        h.addWidget(word)
        sub = UI.QLabel(UI.f('AlphaFold 3 unified submission · {0}@{1}', USER, HOST))
        sub.setObjectName("brandsub")
        h.addWidget(sub)
        h.addStretch(1)
        for txt, delta in (("A-", -1), ("A+", 1)):
            b = UI.QToolButton(); UI.bind(b, 'setText', txt)
            UI.bind(b, 'setToolTip', UI.tr('font size (global)'))
            b.clicked.connect(lambda _=False, d=delta: self._set_font_size(self._font_size + d))
            h.addWidget(b)
        self._lang_btn = UI.QToolButton()
        UI.bind(self._lang_btn, 'setText', (UI.tr('EN') if self._lang == "zh" else UI.tr('中文')))
        UI.bind(self._lang_btn, 'setToolTip', UI.tr('Switch language / 切换语言'))
        self._lang_btn.clicked.connect(
            lambda: self._set_lang("en" if self._lang == "zh" else "zh"))
        h.addWidget(self._lang_btn)
        self._theme_btn = UI.QToolButton()
        UI.bind(self._theme_btn, 'setToolTip', UI.tr('Switch theme / 切换主题'))
        self._update_theme_btn()
        self._theme_menu = QtWidgets.QMenu(self._theme_btn)
        self._theme_actions = {}
        for key, title in _THEME_NAMES.items():
            action = UI.action(self._theme_menu, UI.tr(title)); action.setCheckable(True)
            action.triggered.connect(lambda checked=False, theme=key: self._set_theme(theme))
            self._theme_actions[key] = action
        self._theme_btn.setMenu(self._theme_menu)
        self._theme_btn.setPopupMode(QtWidgets.QToolButton.InstantPopup)
        self._update_theme_btn()
        h.addWidget(self._theme_btn)
        return bar

    # ---- 主题切换(light ccDesign / dark Sanity)----
    def _update_theme_btn(self):
        self._theme_btn.setToolButtonStyle(QtCore.Qt.ToolButtonTextOnly)
        UI.bind(self._theme_btn, 'setText', UI.tr(_THEME_NAMES[self._theme]))
        for key, action in getattr(self, '_theme_actions', {}).items(): action.setChecked(key == self._theme)

    def _toggle_theme(self):
        keys = list(_THEMES)
        self._set_theme(keys[(keys.index(self._theme)+1) % len(keys)])

    def _set_theme(self, theme):
        if theme not in _THEMES: return
        self._theme = theme
        self._settings.setValue("theme", self._theme)
        app = QtWidgets.QApplication.instance()
        # 主题 QSS + 字号规则合并成一次 setStyleSheet:一次全窗口 restyle,不重绘两遍
        _apply_theme(app, self._theme, getattr(self, "_font_qss", ""))
        self._retint()
        self.statusBar().showMessage(UI.render(UI.f('theme: {0}', UI.tr(_THEME_NAMES[self._theme]))), 2000)

    def _retint(self):
        """切主题后,重建期写死颜色的地方统一重上色:页签图标、? 帮助钮、
        screen 页 metrics 行;Results 区整块重渲(表格/图/报告按新调色板重建)。"""
        if qta:
            for _i, _n in enumerate(["fa5s.wrench", "fa5s.rocket", "fa5s.project-diagram",
                                     "fa5s.cut", "fa5s.tachometer-alt", "fa5s.chart-bar",
                                     "fa5s.question-circle"]):
                self.tabs.setTabIcon(_i, qta.icon(_n, color=CC["muted"]))
            for b in self.findChildren(QtWidgets.QToolButton):
                if b.property("help"):
                    b.setIcon(qta.icon("fa5s.question-circle", color=CC["muted_soft"]))
        for sc in (getattr(self, "pd", None), getattr(self, "sc", None)):
            if sc is not None:
                sc.metrics.setStyleSheet(f"color:{CC['primary']}")
        self._update_theme_btn()
        if hasattr(self,"help_body"): self._help_article()
        if hasattr(self,"setup_tree_view"): self._setup_tree(reset_checks=False)
        if getattr(self,"_help_dlg",None) and self._help_dlg.isVisible(): self._show_help(self._help_key)
        if getattr(self, "_res_nb", None) is not None:
            self._res_render()   # 只有 Results 已渲染过才重建(否则白读一轮网络盘)

    # ---- 每页顶部:serif 大标题 + "?" 帮助钮(3 点页面说明收进弹窗,不占版面)----
    def _intro(self, key):
        row = QtWidgets.QHBoxLayout(); row.setSpacing(4)
        title = UI.QLabel(UI.tr(_PAGE_TITLES.get(key, key.title())))
        title.setObjectName("pagehead")
        row.addWidget(title)
        row.addWidget(self._help_button("page_" + key))
        if key in ("submit", "pulldown", "scan"):
            # (#14)表达式语法速查:需要写表达式的页面,标题行随手可点
            # (与 Submit 页 "2 · Expressions" 旁的 ? 同一个弹窗)
            row.addSpacing(10)
            b = UI.QPushButton(UI.tr('Expression syntax examples'))
            UI.bind(b, 'setToolTip', UI.tr('expression syntax cheat sheet (same content as the ? next to Expressions)'))
            if qta:
                b.setIcon(self._ic("fa5s.list-ul"))
            b.clicked.connect(lambda _=False: self._show_help("expressions"))
            row.addWidget(b)
        row.addStretch(1)
        return row

    def _hint_label(self, key):
        """页面内的零散描述句(双语,文案在 af3_ui_help.UI_HINTS)。
        节标题 / 参数名 / 模式名保持英文;只有描述句随 中/EN 切换。"""
        lb = UI.QLabel()
        lb.setWordWrap(True)
        lb.setProperty("caption", True)
        zh, en = getattr(H, "UI_HINTS", {}).get(key, (key, key))
        UI.bind(lb, 'setText', (zh if self._lang == "zh" else en))
        self._hint_labels.append((lb, key))
        return lb

    def _set_lang(self, lang):
        scroll=self.help_body.verticalScrollBar()
        help_position=scroll.value()/max(1,scroll.maximum())
        self._lang = lang
        UI.set_language(lang)
        for lb, key in self._hint_labels:
            if isValid(lb): UI.bind(lb, 'setText', H.UI_HINTS[key][0 if lang == "zh" else 1])
        names = ("Setup", "Submit", "Pulldown", "Scan", "Dashboard", "Results", "Help")
        for i, name in enumerate(names): self.tabs.setTabText(i, UI.render(UI.tr(name)))
        UI.bind(self._lang_btn, 'setText', (UI.tr('EN') if lang == "zh" else UI.tr('中文')))
        self._settings.setValue("lang", lang)
        self._status_home()
        self._setup_tree(reset_checks=False)
        self._dash_fill()
        self._res_fill_combo()
        self._help_filter()
        QtCore.QTimer.singleShot(0,lambda:scroll.setValue(round(help_position*scroll.maximum())))
        if getattr(self,'_pae_detail_refresh',None):self._pae_detail_refresh()
        if getattr(self, "_help_key", None) and getattr(self, "_help_dlg", None) and self._help_dlg.isVisible():
            self._show_help(self._help_key)
        if getattr(self, "_dash_detail_batch", None): self._dash_detail()

    def _status_home(self):
        self.statusBar().showMessage(UI.render(UI.render(UI.f("Output: {0} · Accepted cluster jobs continue after this window closes.", B.HOST_OUTPUT))))

    def _set_font_size(self, n, quiet=False):
        n = max(8, min(18, int(n)))
        self._font_size = n
        app = QtWidgets.QApplication.instance()
        f = app.font(); f.setPointSize(n); app.setFont(f)
        if _MONO is not None:
            _MONO.setPointSize(n)
            for w in self._mono_widgets:
                if isValid(w): w.setFont(_MONO)
        # (#8)全局生效:qt-material 会给按钮/标签等写死字号,app.setFont 压不住;
        # 等优先级下后写的 QSS 赢——把这条规则追加在主题之后,逐次替换不累积。
        targets = ("QWidget, QLabel, QPushButton, QToolButton, QCheckBox, QRadioButton, "
                   "QGroupBox, QTabBar, QTableWidget, QTreeWidget, QHeaderView, QComboBox, QLineEdit, "
                   "QPlainTextEdit, QSpinBox, QMenu, QStatusBar, QToolTip")
        self._font_qss = (f"{targets} {{ font-size: {n}pt; }}\n"
                          f"QTabBar::tab {{ font-size: {n}pt; }}\nQLabel#pagehead {{ font-size: {n + 7}pt; }}")   # (#7)页标题大两号
        ss = app.styleSheet().split(_FONT_RULE_MARK)[0]
        app.setStyleSheet(ss + f"\n{_FONT_RULE_MARK}\n{self._font_qss}\n")
        self._settings.setValue("fontSize", n)
        if not quiet:
            self.statusBar().showMessage(UI.render(UI.f('font size: {0} pt', n)), 2000)

    def _selectable_labels(self, root):
        """(#8)页面上所有 QLabel 文字允许鼠标划选复制(下拉项除外,用户没要)。"""
        for lb in root.findChildren(QtWidgets.QLabel):
            lb.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)

    def _wrap_scroll(self, w):
        """(#1)每页外包滚动区:窗口上下左右都能缩,内容放不下时自动出滚动条。
        (#4)顺带把顶层布局间距收紧到约一半。"""
        lay = w.layout()
        if lay is not None:
            lay.setSpacing(4)
            lay.setContentsMargins(10, 6, 10, 6)
            if isinstance(lay, QtWidgets.QFormLayout):
                lay.setVerticalSpacing(4)
                lay.setHorizontalSpacing(8)
        sa = QtWidgets.QScrollArea()
        sa.setWidget(w)
        sa.setWidgetResizable(True)
        sa.setFrameShape(QtWidgets.QFrame.NoFrame)
        return sa

    @staticmethod
    def _feedback(out, *lines):
        panel = getattr(out, '_feedback_panel', None)
        if panel is not None: panel.set_summary(*lines)

    def _exec(self,args,dry,buttons,out):
        command = '$ ' + B.cmdline_str(args)
        UI.bind(out, 'setPlainText', command)
        out.document().setMaximumBlockCount(2000)
        self._busy(buttons,True)
        name = str(args[args.index('--name')+1]) if '--name' in args else str(args[0])
        activity = {'latest':'', 'attention':'', 'plan':''}
        running = UI.tr('Checking the execution plan…' if dry else 'Preparing the operation…')
        next_step = UI.tr('Preview does not submit jobs.' if dry else 'Wait for confirmation; command success is not prediction completion.')
        def summary(status, next_step):
            detail = activity['attention'] or activity['latest']
            self._feedback(out, status, activity['plan'] or UI.f('Task: {0}',name),
                           UI.f('Attention: {0}' if activity['attention'] else 'Latest: {0}', detail) if detail else UI.tr('Checking inputs and available data…'), next_step)
        def observe(line):
            line=' '.join(line.split())
            if not line:return
            activity['latest']=line[:220]
            if re.search(r'^(?:计划[:：]|Plan:)|\b\d+\s+tasks\b',line,re.I):activity['plan']=line[:220]
            if re.search(r'ERROR|WARNING|WARN\b|失败|警告|冲突',line,re.I):activity['attention']=line[:220]
        summary(running,next_step)
        def append(err,line):
            if not self._closing and isValid(out):
                out.appendPlainText(line); observe(line); summary(running,next_step)
        def progress(line):
            if self._q.qsize() < 256: self._q.put((append,None,line))
        def done(err,res):
            if self._closing or not isValid(self): return
            self._busy(buttons,False)
            if not isValid(out): return
            if err:
                out.appendPlainText('ERROR: '+str(err)); observe('ERROR: '+str(err))
                summary(UI.tr('Operation failed.'),UI.tr('Open details, fix the reported issue, then retry.')); return
            rc,stdout,stderr=res
            for line in (stdout+'\n'+stderr).splitlines():observe(line)
            if rc == 42:
                review = self._empty_msa_review(stdout + '\n' + stderr)
                if review is not None:
                    UI.bind(out, 'setPlainText', command+'\n'+stdout+('\n'+stderr if stderr else ''))
                    if dry:
                        summary(UI.tr('Preview found cached inputs with empty fields.'),
                                UI.tr('Choose whether to reuse or regenerate these inputs when submitting.'))
                        return
                    policy = self._choose_empty_msa_policy(review)
                    if self._closing or not isValid(self): return
                    if policy is None:
                        summary(UI.tr('Cancelled. No prediction submitted.'),
                                UI.tr('Existing MSA files were left unchanged.'))
                        return
                    self._exec(self._args_with_empty_msa_policy(args, policy), dry, buttons, out)
                    return
            # Successful commands also include cache hits and PAE analysis; do not claim a submission.
            status = UI.tr('Preview complete (not submitted)' if dry else 'Command completed; check task status in Dashboard.') if rc==0 else UI.f('Failed, exit code {0}',rc)
            next_step = UI.tr('Review the plan and any warnings before submitting.' if dry else 'Prediction progress and failures are shown in Dashboard.') if rc==0 else UI.tr('Open details, fix the reported issue, then retry.')
            UI.bind(out, 'setPlainText', command+'\n'+stdout+('\n'+stderr if stderr else '')+'\n'+UI.render(status))
            summary(status,next_step)
        enqueue(self._q,lambda:B.run_af3_stream(args,progress,timeout=1800),done)

    @staticmethod
    def _empty_msa_review(output):
        prefix = 'MSA_REUSE_REVIEW_JSON='
        for line in output.splitlines():
            if line.startswith(prefix):
                try:
                    review = json.loads(line[len(prefix):])
                except (TypeError, ValueError):
                    continue
                if (isinstance(review, dict) and isinstance(review.get('empty'), list)
                        and review['empty'] and all(isinstance(item, dict) and isinstance(item.get('path'), str)
                                                    and isinstance(item.get('fields'), list) for item in review['empty'])):
                    return review
        return None

    @staticmethod
    def _args_with_empty_msa_policy(args, policy):
        clean = []
        skip = False
        for arg in args:
            if skip:
                skip = False
            elif arg == '--empty-msa-policy':
                skip = True
            elif not str(arg).startswith('--empty-msa-policy='):
                clean.append(arg)
        return clean + ['--empty-msa-policy', policy]

    def _choose_empty_msa_policy(self, review):
        message = QtWidgets.QMessageBox(self)
        message.setWindowTitle(UI.render(UI.tr('MSA inputs need your decision')))
        message.setIcon(QtWidgets.QMessageBox.Warning)
        message.setTextFormat(QtCore.Qt.PlainText)
        message.setText(UI.render(UI.f('{0} cached inputs contain empty MSA or template fields.', len(review['empty']))))
        message.setInformativeText(UI.render(UI.tr(
            'Empty fields are valid AF3 input, but may omit information you expect. Reuse them as they are, generate MSA again in a new directory, or cancel. Existing files are kept.')))
        details = []
        for item in review['empty']:
            details.append(item['path'] + '\n  ' + ', '.join(str(field) for field in item['fields']))
        message.setDetailedText('\n\n'.join(details))
        reuse = message.addButton(UI.render(UI.tr('Reuse these inputs')), QtWidgets.QMessageBox.AcceptRole)
        recompute = message.addButton(UI.render(UI.tr('Generate MSA again')), QtWidgets.QMessageBox.ActionRole)
        cancel = message.addButton(UI.render(UI.tr('Cancel')), QtWidgets.QMessageBox.RejectRole)
        message.setDefaultButton(cancel)
        message.setEscapeButton(cancel)
        message.exec()
        if message.clickedButton() is reuse:
            return 'reuse'
        if message.clickedButton() is recompute:
            return 'recompute'
        return None

    def _adv_options(self, scan_mode=False):
        """(#13)Advanced options:默认折叠,点 ▸/标题展开。
        标题用普通 QLabel(与 "1 · Mode" 节标题同样式)+ "?" 帮助钮。"""
        wrap = QtWidgets.QWidget()
        wl = QtWidgets.QVBoxLayout(wrap)
        wl.setContentsMargins(0, 2, 0, 2); wl.setSpacing(2)
        head = QtWidgets.QHBoxLayout(); head.setSpacing(4)
        # (#13)与普通按钮同款(如 Preview plan);▸/▾ 箭头跟在文字后面
        toggle = UI.QPushButton(UI.tr('Advanced options'))
        toggle.setIcon(self.style().standardIcon(QtWidgets.QStyle.SP_ArrowRight))
        UI.bind(toggle, 'setToolTip', UI.tr('show / hide advanced options'))
        toggle.setCursor(QtCore.Qt.PointingHandCursor)
        head.addWidget(toggle)
        head.addWidget(self._help_button("adv_options"))
        head.addStretch(1)
        wl.addLayout(head)
        body = QtWidgets.QWidget()
        grid = QtWidgets.QGridLayout(body)
        grid.setContentsMargins(24, 2, 0, 2)   # 内容相对标题缩进,层级一眼可读
        body.setVisible(False)

        def _toggle(*_):
            # 用 isHidden 而不是 not isVisible:窗口未 show 时 isVisible 恒 False
            vis = body.isHidden()
            body.setVisible(vis)
            toggle.setIcon(self.style().standardIcon(QtWidgets.QStyle.SP_ArrowDown if vis else QtWidgets.QStyle.SP_ArrowRight))
        toggle.clicked.connect(_toggle)

        def le(placeholder, default=""):
            e = UI.QLineEdit(default)
            UI.bind(e, 'setPlaceholderText', placeholder)
            return e
        # auto 型参数留空 = 交给 af3.py(保持与 CLI 完全等价);
        # 默认值写进 placeholder 灰字(num-seeds 按页面模式区分),看得见但不覆盖、不误读为 0。
        ns = le(("default: 4 seeds (af3.py scan)" if scan_mode
                 else "default: 1 random seed (af3.py)"), "")
        sd = le("blank = automatic seeds; e.g. 1,2,3")
        pt = le("blank = saved GPU resource policy")
        mc = le("blank = saved concurrency limit", "")
        mp = le("blank = configured MSA partition", "")
        bd = le("none; e.g. A:145:SG->C:1:C04;...")
        uc = le("none (custom ligand CCD .cif path)")
        tf = UI.QCheckBox(UI.tr('--template-free'))
        mf = UI.QCheckBox(UI.tr('--msa-free'))
        fc = UI.QCheckBox(UI.tr('--force'))
        sm = UI.QCheckBox(UI.tr('--shared-msa')) if scan_mode else None

        def row_(r, c, label, w, key):
            grid.addWidget(UI.QLabel(label), r, c)
            grid.addWidget(w, r, c + 1)
            grid.addWidget(self._help_button(key), r, c + 2)

        row_(0, 0, "--num-seeds", ns, "num_seeds")
        row_(0, 3, "--seeds", sd, "seeds")
        row_(1, 0, "--partition", pt, "partition")
        row_(1, 3, "--max-concurrent", mc, "max_conc")
        row_(2, 0, "--bonds", bd, "bonds")
        row_(2, 3, "--user-ccd", uc, "user_ccd")
        grid.addWidget(tf, 3, 0, 1, 2); grid.addWidget(self._help_button("template_free"), 3, 2)
        grid.addWidget(mf, 3, 3, 1, 2); grid.addWidget(self._help_button("msa_free"), 3, 5)
        grid.addWidget(fc, 4, 0, 1, 2); grid.addWidget(self._help_button("force"), 4, 2)
        if sm is not None:
            grid.addWidget(sm, 4, 3, 1, 2); grid.addWidget(self._help_button("shared_msa"), 4, 5)
        row_(5, 0, "--msa-partition", mp, "msa_partition")
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(4, 1)

        def get():
            def gi(w):
                try:
                    return int(w.text().strip())
                except Exception:
                    return 0
            return {"num_seeds": gi(ns), "seeds": sd.text().strip(),
                    "partition": pt.text().strip(), "max_conc": gi(mc),
                    "msa_partition": mp.text().strip(),
                    "template_free": tf.isChecked(), "msa_free": mf.isChecked(),
                    "force": fc.isChecked(), "shared_msa": bool(sm and sm.isChecked()),
                    "bonds": bd.text().strip(), "user_ccd": uc.text().strip()}
        wl.addWidget(body)
        return wrap, get

    # ================================================================ Setup
    def _build_setup(self):
        """(#2)首屏:部署路径/分区确认与修改(写入独立用户配置 JSON),
        右侧目录结构图。新用户 clone 下来第一站。"""
        w = QtWidgets.QWidget(); UI.tab(self.tabs, self._wrap_scroll(w), UI.tr('Setup'))
        lay = QtWidgets.QVBoxLayout(w)
        lay.addLayout(self._intro("setup"))
        split = UI.ResponsiveColumns(threshold=1050)
        lay.addWidget(split, 1)
        left = QtWidgets.QWidget()
        form = QtWidgets.QFormLayout(left)
        form.setVerticalSpacing(12)
        form.setRowWrapPolicy(QtWidgets.QFormLayout.WrapLongRows)
        heading = UI.QLabel("Workspace"); heading.setObjectName("sectionhead"); form.addRow(heading)
        cfg = B.current_config()
        labels = [
            ("HOST_BASE", "工作目录"),
            ("HOST_SIF", "AF3 container image (.sif)"),
            ("HOST_DB_SOURCE", "AF3 database dir"),
            ("CONTAINER_RUNTIME", "Container runtime"),
            ("CONTAINER_MODULE", "Environment module (optional)"),
            ("MSA_PARTITION", "MSA (CPU) partition"),
            ("INF_PARTITION", "Infer (GPU) partition"),
            ("INF_FALLBACK_PARTITION", "Fallback GPU partition (optional)"),
            ("AUX_PARTITION", "Controller CPU partition (optional)"),
            ("HOST_SSD_CACHE", "Node-local database cache (optional)"),
            ("MSA_MAX_CONCURRENT", "MSA max concurrent"),
            ("INF_MAX_CONCURRENT", "Infer max concurrent"),
            ("INF_BIG_TOKEN", "Big-token threshold"),
            ("INF_MAX_TOKEN", "Max token (hard reject above)"),
        ]
        advanced=QtWidgets.QWidget();advanced_form=QtWidgets.QFormLayout(advanced)
        advanced.setVisible(False)
        self.setup_edits = {}
        for k, lab in labels:
            e = UI.QLineEdit(str(cfg["editable"].get(k) or ""))
            e.setMinimumWidth(220)
            placeholders = {
                "HOST_BASE": "/path/to/your/workspace",
                "HOST_SIF": "/path/to/your/alphafold3.sif",
                "HOST_DB_SOURCE": "/path/to/your/af3_databases",
                "HOST_SSD_CACHE": "/path/to/your/node_local_cache",
                "CONTAINER_RUNTIME": "singularity / apptainer",
            }
            if k in placeholders: e.setPlaceholderText(placeholders[k])
            if k == "HOST_BASE" and os.environ.get("AF3_BASE", "").strip() and not os.environ.get("AF3_SNAPSHOT"):
                e.setReadOnly(True)
                e.setToolTip("AF3_BASE")
            rw = QtWidgets.QHBoxLayout()
            rw.addWidget(e, 1); rw.addWidget(self._help_button("setup_" + k.lower()))
            (advanced_form if B.EDITABLE_CONFIG[k][0] == "int" else form).addRow(UI.QLabel(lab),rw)
            self.setup_edits[k] = e
        adv_button=UI.QPushButton(UI.tr('Resource limits · expand'));adv_button.setCheckable(True)
        adv_button.toggled.connect(advanced.setVisible)
        
        # (#2)派生路径:默认跟随 HOST_BASE 联动,但可手工改(改过的字段不再被联动覆盖)
        heading = UI.QLabel("Compute resource paths"); heading.setObjectName("sectionhead"); form.addRow(heading)
        self.setup_derived = {}
        self._setup_derived_dirty = set()
        self.setup_msa_backup_edits = []
        self._setup_msa_backup_rows = []
        for k, lab in [("HOST_OUTPUT", "Output dir (derived, editable)"),
                       ("HOST_MSA_DATA", "MSA data pool (derived, editable)"),
                       ("HOST_MODELS", "Model weights (derived, editable)"),
                       ("HOST_CACHE", "Cache dir (derived, editable)"),
                       ("HOST_JAX_CACHE", "JAX compile cache (derived, editable)")]:
            e = UI.QLineEdit(str(cfg["editable"].get(k) or cfg["derived"].get(k) or ""))
            e.setMinimumWidth(220)
            e.setPlaceholderText("/path/to/your/" + {"HOST_OUTPUT":"results", "HOST_MSA_DATA":"msa_pool", "HOST_MODELS":"af3_models", "HOST_CACHE":"application_cache", "HOST_JAX_CACHE":"jax_cache"}[k])
            e.textEdited.connect(lambda _t="", kk=k: self._setup_derived_dirty.add(kk))
            rw = QtWidgets.QHBoxLayout()
            if k == "HOST_MSA_DATA":
                self.setup_msa_add = UI.QPushButton("+")
                self.setup_msa_add.setFixedWidth(32)
                self.setup_msa_add.setStyleSheet('padding: 0;')
                UI.bind(self.setup_msa_add, 'setToolTip', UI.tr('Add an MSA backup path (up to three paths in total)'))
                UI.bind(self.setup_msa_add, 'setAccessibleName', UI.tr('Add MSA backup path'))
                self.setup_msa_add.clicked.connect(lambda: self._setup_add_msa_backup())
                rw.addWidget(self.setup_msa_add)
            rw.addWidget(e, 1); rw.addWidget(self._help_button("setup_" + k.lower()))
            if k == "HOST_MSA_DATA":
                self._setup_msa_paths_layout = QtWidgets.QVBoxLayout()
                self._setup_msa_paths_layout.setContentsMargins(0, 0, 0, 0)
                self._setup_msa_paths_layout.addLayout(rw)
                form.addRow(UI.QLabel(lab), self._setup_msa_paths_layout)
                for path in cfg['editable'].get('MSA_BACKUP_DIRS', []):
                    self._setup_add_msa_backup(path)
                self.setup_msa_sync_button = UI.QPushButton(UI.tr('Check MSA synchronization'))
                self.setup_msa_sync_button.clicked.connect(lambda: self._check_msa_sync(manual=True))
                form.addRow(self.setup_msa_sync_button)
            else:
                form.addRow(UI.QLabel(lab), rw)
            self.setup_derived[k] = e
        # Executable paths describe the running installation.
        heading = UI.QLabel("Program information"); heading.setObjectName("sectionhead"); form.addRow(heading)
        self.setup_misc = {}
        self._setup_misc_dirty = set()
        for k, lab in [("AF3_PY", "程序位置（固定）"),
                       ("PYTHON", "Python interpreter (current)")]:
            e = UI.QLineEdit(str(cfg["derived"].get(k) or ""))
            e.setMinimumWidth(220)
            e.textEdited.connect(lambda _t="", kk=k: self._setup_misc_dirty.add(kk))
            rw = QtWidgets.QHBoxLayout()
            rw.addWidget(e, 1); rw.addWidget(self._help_button("setup_" + k.lower()))
            form.addRow(UI.QLabel(lab), rw)
            e.setReadOnly(True)
            self.setup_misc[k] = e
        form.addRow(adv_button); form.addRow(advanced)
        self.setup_resource_toggle = adv_button
        self._setup_orig_base = self.setup_edits["HOST_BASE"].text().strip()
        self._setup_orig_derived = {k:e.text().strip() for k,e in self.setup_derived.items()}
        self._setup_orig_misc = {k: e.text().strip() for k, e in self.setup_misc.items()}
        self.setup_edits["HOST_BASE"].textChanged.connect(self._setup_derive)
        row = QtWidgets.QHBoxLayout()
        b1 = UI.QPushButton(UI.tr('保存用户配置'))
        b1.setObjectName("primary")
        b1.clicked.connect(self._setup_apply)
        b2 = UI.QPushButton(UI.tr('复制配置 JSON'))
        b2.clicked.connect(lambda: self._copy(self._setup_config_lines(), "Config block"))
        b3 = UI.QPushButton(UI.tr('检测资源'))
        b3.clicked.connect(self._check_resources)
        for b in (b1, b2, b3):
            row.addWidget(b)
        row.addStretch(1)
        form.addRow(row)
        split.addPanel(left, 3)
        right = QtWidgets.QWidget()
        rl = QtWidgets.QVBoxLayout(right)
        rl.addWidget(UI.QLabel(UI.tr('Directory map')))
        self.setup_directory_map = UI.QTextBrowser()
        self.setup_directory_map.setReadOnly(True)
        self.setup_directory_map.setWordWrapMode(QtGui.QTextOption.WrapAnywhere)
        self.setup_directory_map.setMinimumHeight(270)
        self.setup_directory_map.setMaximumHeight(390)
        self.setup_directory_map.setObjectName('console')
        rl.addWidget(self.setup_directory_map)
        rl.addWidget(UI.QLabel(UI.tr('Configuration summary')))
        self.setup_tree_view = UI.QTextBrowser()
        self.setup_tree_view.setWordWrapMode(QtGui.QTextOption.WrapAnywhere)
        self.setup_tree_view.setReadOnly(True)
        self.setup_tree_view.setMinimumHeight(260)
        self.setup_tree_view.setMaximumHeight(350)
        self.setup_tree_view.setObjectName("console")   # 目录地图 = 浅奶油输出卡
        self.setup_tree_view.setOpenExternalLinks(False)
        rl.addWidget(self.setup_tree_view, 1)
        rl.addStretch(1)
        split.addPanel(right, 2)
        for e in {**self.setup_edits, **self.setup_derived}.values():
            e.textChanged.connect(lambda: self._setup_tree())
        self._setup_tree()

    def _setup_add_msa_backup(self, path=''):
        if len(self.setup_msa_backup_edits) >= 2:
            return
        row = QtWidgets.QWidget()
        layout = QtWidgets.QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        remove = UI.QPushButton('−')
        remove.setFixedWidth(32)
        remove.setStyleSheet('padding: 0;')
        UI.bind(remove, 'setToolTip', UI.tr('Remove this path from settings only; no files are deleted'))
        UI.bind(remove, 'setAccessibleName', UI.tr('Remove MSA backup path'))
        edit = UI.QLineEdit(str(path))
        edit.setMinimumWidth(220)
        edit.setPlaceholderText('/path/to/your/msa_backup')
        UI.bind(edit, 'setToolTip', UI.tr('MSA backup path; complete new outputs are copied here'))
        layout.addWidget(remove); layout.addWidget(edit, 1)
        self._setup_msa_paths_layout.addWidget(row)
        self.setup_msa_backup_edits.append(edit)
        self._setup_msa_backup_rows.append(row)
        remove.clicked.connect(lambda: self._setup_remove_msa_backup(edit, row))
        edit.textChanged.connect(lambda: self._setup_tree() if hasattr(self, 'setup_tree_view') else None)
        self.setup_msa_add.setEnabled(len(self.setup_msa_backup_edits) < 2)
        if hasattr(self, 'setup_tree_view'):
            self._setup_tree()

    def _setup_remove_msa_backup(self, edit, row):
        # Removing a configuration field never performs any filesystem operation.
        self.setup_msa_backup_edits.remove(edit)
        self._setup_msa_backup_rows.remove(row)
        self._setup_msa_paths_layout.removeWidget(row)
        row.setParent(None); row.deleteLater()
        self.setup_msa_add.setEnabled(True)
        self._setup_tree()

    def _setup_msa_backups(self):
        return [edit.text().strip() for edit in self.setup_msa_backup_edits if edit.text().strip()]

    def _setup_values(self):
        values = {key: edit.text().strip() for key, edit in {**self.setup_edits, **self.setup_derived}.items()}
        values['MSA_BACKUP_DIRS'] = self._setup_msa_backups()
        return values

    def _startup_msa_sync(self):
        if self._closing or not isValid(self) or self._msa_startup_checked:
            return
        self._msa_startup_checked = True
        roots = self._msa_startup_roots
        if len(roots) > 1 and roots == tuple(B.msa_pool_paths()):
            self._check_msa_sync(roots=roots)

    def _msa_sync_current(self, roots):
        return not self._closing and isValid(self) and tuple(B.msa_pool_paths()) == tuple(roots)

    @staticmethod
    def _msa_sync_details(result):
        # Plain text prevents filesystem names from becoming rich-text links.
        sections = []
        for key, title in [('roots', 'MSA paths'), ('copies', 'New copies'), ('conflicts', 'Conflicts'),
                           ('invalid', 'Incomplete or unsupported data'), ('errors', 'Errors')]:
            entries = result.get(key) or []
            if not entries:
                continue
            lines = [UI.render(UI.tr(title)) + ':']
            for entry in entries:
                if isinstance(entry, dict):
                    shown = {key: entry[key] for key in ('source', 'destination', 'path', 'relative',
                                                        'files', 'bytes', 'reason', 'sources') if key in entry}
                    lines.append(json.dumps(shown, ensure_ascii=False, indent=2))
                else:
                    lines.append(str(entry))
            sections.append('\n'.join(lines))
        return '\n\n'.join(sections)

    def _msa_sync_message(self, text, details='', question=False, warning=False):
        message = QtWidgets.QMessageBox(self)
        message.setWindowTitle(UI.render(UI.tr('MSA synchronization')))
        message.setTextFormat(QtCore.Qt.PlainText)
        message.setText(text)
        message.setIcon(QtWidgets.QMessageBox.Question if question else
                        (QtWidgets.QMessageBox.Warning if warning else QtWidgets.QMessageBox.Information))
        if details:
            message.setDetailedText(details)
        if question:
            message.setStandardButtons(QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No)
            message.setDefaultButton(QtWidgets.QMessageBox.No)
            message.setEscapeButton(QtWidgets.QMessageBox.No)
        else:
            message.setStandardButtons(QtWidgets.QMessageBox.Ok)
        return message.exec()

    def _check_msa_sync(self, manual=False, roots=None):
        if self._closing or not isValid(self) or self._msa_sync_busy:
            return
        roots = tuple(roots if roots is not None else B.msa_pool_paths())
        if manual:
            normalize = lambda path: os.path.normcase(os.path.abspath(os.path.expanduser(path)))
            shown = [self.setup_derived['HOST_MSA_DATA'].text().strip(), *self._setup_msa_backups()]
            if tuple(normalize(path) for path in shown if path) != tuple(normalize(path) for path in roots):
                self._msa_sync_message(UI.render(UI.tr('Save the MSA paths before checking synchronization.')))
                return
        if len(roots) < 2:
            if manual:
                self._msa_sync_message(UI.render(UI.tr('Add and save at least one MSA backup path first.')))
            return
        self._msa_sync_busy = True
        self.setup_msa_sync_button.setEnabled(False)
        self.statusBar().showMessage(UI.render(UI.tr('Checking MSA pools in the background…')))
        def done(error, plan):
            if not self._msa_sync_current(roots):
                if not self._closing and isValid(self):
                    self._msa_sync_busy = False
                    self.setup_msa_sync_button.setEnabled(True)
                return
            self._msa_sync_busy = False
            self.setup_msa_sync_button.setEnabled(True)
            if error:
                self._msa_sync_message(UI.render(UI.tr('MSA pool check failed. No files were copied.')), str(error), warning=True)
                return
            copies = plan.get('copies') or []
            counts = [len(plan.get(key) or []) for key in ('conflicts', 'invalid', 'errors')]
            summary = UI.render(UI.f('Conflicts: {0}; incomplete/unsupported: {1}; errors: {2}.', *counts))
            details = self._msa_sync_details(plan)
            if not copies:
                self.statusBar().showMessage(UI.render(UI.tr('MSA pool check finished.')), 8000)
                if manual or any(counts):
                    self._msa_sync_message(UI.render(UI.tr('No new complete products are available to copy.')) + '\n' + summary,
                                           details, warning=any(counts))
                return
            size = sum(int(item.get('bytes') or 0) for item in copies)
            size_text = '{:.1f} MiB'.format(size / (1024 * 1024))
            prompt = UI.render(UI.f('Copy {0} new product units ({1}) between the saved MSA paths?', len(copies), size_text))
            prompt += '\n\n' + UI.render(UI.tr('Only missing complete products are added. Existing files are never overwritten or deleted.'))
            prompt += '\n' + summary
            accepted = self._msa_sync_message(prompt, details, question=True)
            if accepted == QtWidgets.QMessageBox.Yes and self._msa_sync_current(roots):
                self._execute_msa_sync(roots, plan)
        enqueue(self._q, lambda: B.scan_msa_pools(list(roots)), done)

    def _execute_msa_sync(self, roots, plan):
        if not self._msa_sync_current(roots) or self._msa_sync_busy:
            return
        self._msa_sync_busy = True
        self.setup_msa_sync_button.setEnabled(False)
        self.statusBar().showMessage(UI.render(UI.tr('Copying complete MSA products in the background…')))
        def done(error, result):
            if self._closing or not isValid(self):
                return
            self._msa_sync_busy = False
            self.setup_msa_sync_button.setEnabled(True)
            if error:
                self._msa_sync_message(UI.render(UI.tr('MSA synchronization stopped. Some products may already have been copied.')),
                                       str(error), warning=True)
                return
            result = dict(result, invalid=plan.get('invalid') or [])
            text = UI.render(UI.f('MSA synchronization finished: {0} copied; {1} skipped; {2} conflicts; {3} incomplete/unsupported; {4} errors.',
                                  result.get('copied', 0), result.get('skipped', 0),
                                  len(result.get('conflicts') or []), len(result['invalid']), len(result.get('errors') or [])))
            self.statusBar().showMessage(text, 15000)
            self._msa_sync_message(text, self._msa_sync_details(result),
                                   warning=bool(result.get('conflicts') or result.get('invalid') or result.get('errors')))
        enqueue(self._q, lambda: B.sync_msa_pools(plan), done)

    def _setup_derive(self):
        base=self.setup_edits["HOST_BASE"].text().strip()
        if not base:return
        original=self._setup_orig_base.rstrip("/\\")
        for key,suffix in (("HOST_OUTPUT","output"),("HOST_MSA_DATA","msa_data"),("HOST_MODELS","models"),("HOST_CACHE","cache"),("HOST_JAX_CACHE","af3_buckets_cache")):
            field=self.setup_derived[key]
            old=self._setup_orig_derived.get(key,"")
            # Explicit/shared paths survive changing the user's work directory.
            if key not in self._setup_derived_dirty and (not old or os.path.normpath(old)==os.path.normpath(os.path.join(original,suffix))):
                UI.bind(field, 'setText', os.path.join(base,suffix))
        self._setup_tree()

    def _setup_config_lines(self):
        values={'MSA_BACKUP_DIRS': self._setup_msa_backups()}
        for key,field in {**self.setup_edits,**self.setup_derived}.items():
            value=field.text().strip()
            if B.EDITABLE_CONFIG[key][0]=="int" and value.isdigit():value=int(value)
            values[key]=value
        return json.dumps(values,ensure_ascii=False,indent=2)

    def _check_resources(self):
        values=self._setup_values()
        UI.bind(self.setup_tree_view, 'setPlainText', UI.tr('正在检测资源…'))
        def target():
            config = dict(B.af3.config_snapshot(), **values)
            return B.af3.deployment_checks(config)
        def done(err,result):
            current=self._setup_values()
            if current != values: return
            if err: UI.bind(self.setup_tree_view,'setPlainText',UI.f('read failed: {0}',str(err))); return
            self._setup_checks=result; self._setup_tree(reset_checks=False)
        enqueue(self._q,target,done)

    def _setup_tree(self, reset_checks=True):
        if reset_checks: self._setup_checks = None
        paths={k:e.text().strip() for k,e in {**self.setup_edits,**self.setup_derived}.items()}
        base=paths.get('HOST_BASE','')
        esc=html.escape
        label=lambda text:esc(UI.render(UI.tr(text)))
        rows=['<p style="margin:0 0 10px"><b>'+label('Personal workspace')+'</b><br>'+esc(base)+'</p><table cellspacing="6" width="100%">']
        for key,description in [('HOST_OUTPUT','Plans, logs and prediction results'),('HOST_MSA_DATA','Shared alignment inputs; reused by predictions'),
                                ('HOST_MODELS','AF3 model weights'),('HOST_CACHE','Sequences and application assets'),
                                ('HOST_JAX_CACHE','JAX compilation cache'),('HOST_INFER_DATA','Reusable monomer inference results')]:
            path=paths.get(key,getattr(B,key,''))
            inside=path.startswith(base.rstrip('/\\')+os.sep) if base else False
            # os.path is native to the deployment host; external paths stay explicit.
            shown=os.path.relpath(path,base) if inside else path
            branch=('└── ' if key=='HOST_INFER_DATA' else '├── ') if inside else '↗ '
            rows.append('<tr><td width="40%"><code>'+esc(branch+shown)+'</code></td><td style="color:'+CC['muted']+'">'+label(description)+'</td></tr>')
        for path in self._setup_msa_backups():
            rows.append('<tr><td width="40%"><code>'+esc('↗ '+path)+'</code></td><td style="color:'+CC['muted']+'">'+label('MSA backup; add-only synchronization')+'</td></tr>')
        rows.append('</table>')
        for key,description in [('HOST_SIF','AF3 container image'),('HOST_DB_SOURCE','Sequence and template databases')]:
            rows.append('<p style="margin:8px 0 0"><b>'+label(description)+'</b> · '+esc(paths.get(key,''))+'</p>')
        self.setup_directory_map.setHtml('<html><body style="color:'+CC['ink']+'">'+''.join(rows)+'</body></html>')
        t=lambda key:html.escape(UI.render(UI.tr(key)))
        parts=['<h3>'+t('Program and user data are independent.')+'</h3>']
        for title,value in [('Configuration file',B.R.config_path()),('Installed core',B.AF3_PY)]:
            parts.append('<p><b>'+t(title)+'</b><br>'+html.escape(value)+'</p>')
        overrides = [(key, os.environ[key]) for key in B.R.CONFIG_ENV_KEYS
                     if os.environ.get(key, '').strip() and not os.environ.get('AF3_SNAPSHOT')]
        if overrides:
            parts.append('<h3>'+t('Active environment overrides')+'</h3>')
            for key, value in overrides:
                parts.append('<p><code>'+esc(key)+'</code>: '+esc(value)+'</p>')
            parts.append('<p>'+t('Environment overrides take precedence over saved settings.')+'</p>')
        checks=getattr(self,'_setup_checks',None)
        if checks:
            parts.append('<h3>'+t('Check resources')+'</h3>')
            for check in checks:
                key,ok = check['key'],check['ok']
                color=CC['success' if ok else 'warning']
                parts.append('<p><span style="color:'+color+'">'+t('Passed' if ok else 'Needs configuration')+'</span> · '+esc(key)+'<br>'+esc(str(check.get('message','')))+'</p>')
        else: parts.append('<p>'+t('Check resources to verify paths and permissions.')+'</p>')
        parts.extend('<p>'+t(key)+'</p>' for key in ['Configure resource limits for your cluster.','New submissions pin their plan, inputs and runtime version.'])
        self.setup_tree_view.setHtml(H._document(''.join(parts),CC))

    def _setup_apply(self):
        current=B.current_config()["editable"]
        updates={k:e.text().strip() for k,e in {**self.setup_edits,**self.setup_derived}.items() if e.text().strip()!=str(current.get(k,""))}
        if self._setup_msa_backups() != current.get('MSA_BACKUP_DIRS', []):
            updates['MSA_BACKUP_DIRS'] = self._setup_msa_backups()
        if not updates:
            self.statusBar().showMessage(UI.render(UI.tr('配置没有变化')), 4000);return
        count,path,error=B.update_af3_config(updates)
        if error:UI.QMessageBox.critical(self, UI.tr('配置保存失败'), error);return
        effective = B.current_config()['editable']
        for key, field in {**self.setup_edits, **self.setup_derived}.items():
            field.setText(str(effective.get(key, '')))
        for edit, row in list(zip(self.setup_msa_backup_edits, self._setup_msa_backup_rows)):
            self._setup_remove_msa_backup(edit, row)
        for path in effective.get('MSA_BACKUP_DIRS', []):
            self._setup_add_msa_backup(path)
        self._setup_orig_base=self.setup_edits["HOST_BASE"].text().strip()
        self._setup_orig_derived={k:e.text().strip() for k,e in self.setup_derived.items()}
        self._setup_derived_dirty.clear()
        UI.bind(self, 'setWindowTitle', UI.f("{0}{1}", UI.tr('AF3 Console — '), B.HOST_BASE))
        self._setup_tree()
        self.statusBar().showMessage(UI.render(UI.percent('已保存 %s 项配置：%s', (count,path))), 15000)

    # ================================================================ Submit
    def _build_submit(self):
        w = QtWidgets.QWidget()
        self._submit_tab = w
        UI.tab(self.tabs, self._submit_tab, UI.tr('Submit'))
        # Give the form all remaining space; feedback has four fixed text lines.
        split = QtWidgets.QVBoxLayout(); split.setContentsMargins(0,0,0,0)
        lay = QtWidgets.QVBoxLayout(w)
        lay.addLayout(self._intro("submit"))
        # (#17)--name 提到标题正下方,命名提示紧随其后(三页布局一致)
        nrow = QtWidgets.QHBoxLayout()
        nrow.addWidget(UI.QLabel(UI.tr('--name')))
        self.sub_name = UI.QLineEdit()
        UI.bind(self.sub_name, 'setPlaceholderText', UI.tr('auto (first UniProt id, e.g. P12345_n3)'))
        self.sub_name.setMinimumWidth(360)
        nrow.addWidget(self.sub_name, 1)
        nrow.addWidget(self._help_button("name"))
        lay.addLayout(nrow)
        lay.addWidget(self._hint_label("submit_autoname"))
        lay.addLayout(split, 1)
        left = QtWidgets.QWidget(); ll = QtWidgets.QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.addLayout(self._label_with_help("1 · Mode", "mode"))
        self.sub_mode = QtWidgets.QButtonGroup(left)
        self.sub_mode_map = {}
        mode_grid = QtWidgets.QGridLayout(); mode_grid.setSpacing(10)
        mode_holder=QtWidgets.QWidget();mode_holder.setLayout(mode_grid);mode_holder.setMaximumWidth(980)
        mode_grid.setContentsMargins(0,0,0,0);ll.addWidget(mode_holder)
        mode_hint = UI.QLabel(UI.tr('Choose end-to-end for a first prediction; use other modes to reuse data or analyse domains.'))
        mode_hint.setWordWrap(True); mode_hint.setProperty("caption", True); ll.addWidget(mode_hint)
        for i, m in enumerate(["End-to-end (MSA->infer)", "MSA only (--msa-only)",
                               "Infer only (--infer-only)", "Raw JSON (--json)",
                               "PAE domains (pae)"]):
            rb = UI.QRadioButton(m)
            self.sub_mode.addButton(rb, i); self.sub_mode_map[i] = m
            mode_grid.addWidget(rb, i // 3, i % 3)
            if i == 0:
                rb.setChecked(True)
        # pae 模式参数面板(仅 pae 模式显示;单蛋白 PAE domain 分段)
        self.sub_pae_panel = QtWidgets.QWidget()
        sp = QtWidgets.QGridLayout(self.sub_pae_panel)
        sp.setContentsMargins(24, 0, 0, 0)
        sp.setHorizontalSpacing(6)
        self.sp_cut = UI.QDoubleSpinBox()
        self.sp_cut.setRange(0.5, 30.0); self.sp_cut.setSingleStep(0.5)
        self.sp_cut.setValue(5.0); self.sp_cut.setDecimals(1)
        self.sp_res = UI.QDoubleSpinBox()
        self.sp_res.setRange(0.05, 4.0); self.sp_res.setSingleStep(0.1)
        self.sp_res.setValue(0.5); self.sp_res.setDecimals(2)
        self.sp_mdom = self._spin(2, 200, 10)
        self.sp_dpw = self._spin(1, 10, 1)
        self.sp_mf = self._spin(10, 2000, 250)
        self.sp_maxf = self._spin(0, 20000, 0)
        UI.bind(self.sp_maxf, 'setSpecialValueText', UI.tr('auto (=500)'))
        sp_items = (("--pae-cutoff", self.sp_cut, "pae_cutoff"),
                    ("--pae-resolution", self.sp_res, "pae_resolution"),
                    ("--pae-min-domain", self.sp_mdom, "pae_min_domain"),
                    ("--pae-domains-per-window", self.sp_dpw,
                     "pae_domains_per_window"),
                    ("--pae-min-frag", self.sp_mf, "pae_min_frag"),
                    ("--pae-max-frag", self.sp_maxf, "pae_max_frag"))
        for i, (lab, spw, key) in enumerate(sp_items):
            r, c = divmod(i, 3)
            sp.addWidget(UI.QLabel(lab), r, c * 3)
            sp.addWidget(spw, r, c * 3 + 1)
            sp.addWidget(self._help_button(key), r, c * 3 + 2)
        self.sub_pae_panel.setVisible(False)
        ll.addWidget(self.sub_pae_panel)
        self.sub_json_panel=QtWidgets.QWidget();json_form=QtWidgets.QFormLayout(self.sub_json_panel)
        self.sub_json=UI.QLineEdit(str(self._settings.value("raw_json_path","")))
        UI.bind(self.sub_json, 'setPlaceholderText', UI.tr('集群上的 AF3 JSON 文件绝对路径'))
        self.sub_json.textChanged.connect(lambda text:self._settings.setValue("raw_json_path",text))
        self.sub_json_stage=QtWidgets.QComboBox();UI.items(self.sub_json_stage, [UI.tr('完整流程：MSA + 推理'), UI.tr('仅生成 MSA'), UI.tr('仅推理（JSON 已含 MSA）')])
        json_form.addRow(UI.QLabel(UI.tr('JSON file')),self.sub_json);json_form.addRow(UI.QLabel(UI.tr('Execution stage')),self.sub_json_stage)
        self.sub_json_panel.setVisible(False);ll.addWidget(self.sub_json_panel)
        self.sub_mode.buttonToggled.connect(self._on_sub_mode)
        ll.addLayout(self._label_with_help(
            "2 · Expressions (one task per line; '#' = comment)", "expressions"))
        self.sub_expr = UI.QPlainTextEdit()
        self.sub_expr.setMinimumHeight(80); self.sub_expr.setMaximumHeight(100)
        UI.bind(self.sub_expr, 'setPlaceholderText', UI.tr('P12345\nP12345+Q12345\nP12345:trunc=1-300+l:ATP'))
        self._reg_mono(self.sub_expr)
        ll.addWidget(self.sub_expr)
        row = QtWidgets.QHBoxLayout()
        b = UI.QPushButton(UI.tr('Load expression file…'))
        b.clicked.connect(self._sub_load)
        row.addWidget(b); row.addWidget(self._help_button("load_expr")); row.addStretch(1)
        ll.addLayout(row)
        gb, self.sub_opts = self._adv_options(); ll.addWidget(gb)
        btns = QtWidgets.QHBoxLayout()
        self.sub_parse = UI.QPushButton(UI.tr('Parse preview'))
        self.sub_dry = UI.QPushButton(UI.tr('Preview plan (--dry-run)'))
        self.sub_go = UI.QPushButton(UI.tr('Submit'))
        self.sub_go.setDefault(True)
        self.sub_go.setObjectName("primary")
        if qta:
            self.sub_parse.setIcon(self._ic("fa5s.search"))
            self.sub_dry.setIcon(self._ic("fa5s.eye"))
            self.sub_go.setIcon(self._ic("fa5s.paper-plane", "#ffffff"))
        self.sub_parse.clicked.connect(self._sub_parse)
        self.sub_dry.clicked.connect(lambda: self._sub_run(True))
        self.sub_go.clicked.connect(lambda: self._sub_run(False))
        for b in (self.sub_parse, self.sub_dry): btns.addWidget(b)
        btns.addStretch(1); btns.addWidget(self.sub_go)
        form_scroll=QtWidgets.QScrollArea();form_scroll.setWidgetResizable(True)
        form_scroll.setFrameShape(QtWidgets.QFrame.NoFrame);form_scroll.setWidget(left)
        form_container=QtWidgets.QWidget();form_layout=QtWidgets.QVBoxLayout(form_container)
        form_layout.setContentsMargins(0,0,0,0);form_layout.addWidget(form_scroll,1);form_layout.addLayout(btns)
        form_container.setMinimumHeight(150)
        split.addWidget(form_container, 1)
        self.sub_out = self._out()
        self.sub_output_panel = UI.OutputPanel(self.sub_out, always_open=True)
        split.addWidget(self.sub_output_panel)

    def _sub_load(self):
        p, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, UI.render(UI.tr("Load expressions")), "", UI.render(UI.tr("Text files (*.txt *.tsv *.csv);;All files (*)")))
        if p:
            try:
                UI.bind(self.sub_expr, 'setPlainText', B.read_text(p))
            except Exception as e:
                UI.QMessageBox.critical(self, UI.tr('Read failed'), str(e))

    def _on_sub_mode(self, *_):
        m = self.sub_mode_map[self.sub_mode.checkedId()]
        self.sub_pae_panel.setVisible(m == "PAE domains (pae)")
        self.sub_json_panel.setVisible(m.startswith("Raw JSON"))

    def _sub_mode_key(self):
        m = self.sub_mode_map[self.sub_mode.checkedId()]
        if m.startswith("Raw JSON"):return ["end2end","msa_only","infer_only"][self.sub_json_stage.currentIndex()]
        return {"MSA only (--msa-only)": "msa_only",
                "Infer only (--infer-only)": "infer_only",
                "PAE domains (pae)": "pae"}.get(m, "end2end")

    def _sub_parse(self):
        lines = B.split_lines(self.sub_expr.toPlainText())
        UI.bind(self.sub_out, 'setPlainText', UI.tr(''))
        if not lines:
            UI.bind(self.sub_out,'setPlainText',UI.tr('Fill in the form, then preview the plan.'))
            self._feedback(self.sub_out,UI.tr('No input to parse.'),UI.tr('Fill in the form, then preview the plan.'))
            return
        errors=0
        for ln in lines[:30]:
            ents, err = B.capture_af3_parse(ln, fetch=False)
            if err:
                errors+=1
                self.sub_out.appendPlainText(f"[X] {ln}\n    {err}")
            else:
                self.sub_out.appendPlainText(
                    f"[OK] {ln}\n    -> " + " + ".join(B.entity_summary(e) for e in ents))
        self._feedback(self.sub_out,UI.tr('Expression check complete.'),UI.f('Checked {0} of {1} inputs; {2} errors.',min(len(lines),30),len(lines),errors),
                       UI.tr('Syntax only: sequences not fetched; no prediction submitted.'),
                       UI.tr('Open details, fix the reported issue, then retry.') if errors else UI.tr('Preview the execution plan to check data and task counts.'))

    def _sub_run(self, dry):
        mode = self.sub_mode_map[self.sub_mode.checkedId()]
        json_path = None
        if mode.startswith("Raw JSON"):
            json_path = self.sub_json.text().strip()
            if not json_path:
                UI.bind(self.sub_out, 'setPlainText', UI.tr('请先填写 JSON 文件路径'))
                self._feedback(self.sub_out,UI.tr('请先填写 JSON 文件路径'),UI.tr('No prediction submitted.'));return
        pae_params = None
        if mode == "PAE domains (pae)":
            pae_params = {"pae_cutoff": self.sp_cut.value(),
                          "pae_resolution": self.sp_res.value(),
                          "pae_min_domain": self.sp_mdom.value(),
                          "pae_domains_per_window": self.sp_dpw.value(),
                          "pae_min_frag": self.sp_mf.value(),
                          "pae_max_frag": self.sp_maxf.value()}
        lines = B.split_lines(self.sub_expr.toPlainText())
        name = self.sub_name.text().strip() or B.suggest_run_name(lines)
        args, err = B.build_run_args(lines, mode=self._sub_mode_key(), json_path=json_path,
                                     name=name, opts=self.sub_opts(),
                                     dry_run=dry, user_tag="", pae_params=pae_params)
        if err:
            UI.bind(self.sub_out,'setPlainText',str(err))
            self._feedback(self.sub_out,UI.tr('Invalid input'),str(err),UI.tr('No prediction submitted.'))
            UI.QMessageBox.critical(self, UI.tr('Invalid input'), err); return
        self._exec(args, dry, [self.sub_parse, self.sub_dry, self.sub_go], self.sub_out)

    # ================================================================ Screen
    # (#6)Pulldown / Scan 已拆成两个独立页面,由文件底部的 _ScreenTab 类实现;
    # App.__init__ 里 self.pd / self.sc 各建一页,防误选模式(选错代价巨大)。
    def _spin(self, lo, hi, val):
        s = UI.QSpinBox(); s.setRange(lo, hi); s.setValue(val); return s

    # ================================================================ Dashboard
    def _build_dashboard(self):
        w = QtWidgets.QWidget(); UI.tab(self.tabs, w, UI.tr('Dashboard'))
        lay = QtWidgets.QVBoxLayout(w); lay.setContentsMargins(20, 16, 20, 16); lay.setSpacing(12)
        lay.addLayout(self._intro("dashboard"))
        bar = QtWidgets.QHBoxLayout()
        self.dash_btn = UI.QPushButton(UI.tr('Refresh')); self.dash_btn.clicked.connect(self._dash_refresh)
        self.dash_auto = UI.QCheckBox(UI.tr('auto-refresh every 30s')); self.dash_auto.toggled.connect(self._dash_auto)
        bar.addWidget(self.dash_btn); bar.addWidget(self.dash_auto); bar.addStretch()
        self.dash_filter = UI.QLineEdit(); UI.bind(self.dash_filter, 'setPlaceholderText', UI.tr('name / type / status …'))
        self.dash_filter.setClearButtonEnabled(True); self.dash_filter.setMinimumWidth(220)
        self._dash_ftimer = QtCore.QTimer(self); self._dash_ftimer.setSingleShot(True); self._dash_ftimer.setInterval(150)
        self._dash_ftimer.timeout.connect(self._dash_fill); self.dash_filter.textChanged.connect(self._dash_ftimer.start)
        bar.addWidget(self.dash_filter); lay.addLayout(bar)
        self.dash_error = QtWidgets.QWidget()
        el = QtWidgets.QVBoxLayout(self.dash_error); el.setContentsMargins(0, 0, 0, 0)
        self.dash_error_toggle = UI.QPushButton(); self.dash_error_toggle.setCheckable(True)
        self.dash_error_toggle.setStyleSheet("text-align: left; color: #b26a00")
        self.dash_error_details = UI.QPlainTextEdit(); self.dash_error_details.setReadOnly(True); self.dash_error_details.setMaximumHeight(100)
        self.dash_error_details.hide(); self.dash_error_toggle.toggled.connect(self.dash_error_details.setVisible)
        el.addWidget(self.dash_error_toggle); el.addWidget(self.dash_error_details); self.dash_error.hide(); lay.addWidget(self.dash_error)
        cards = QtWidgets.QHBoxLayout(); self.dash_cards = []
        for _ in range(4):
            lb = UI.QLabel(UI.tr('—')); lb.setProperty("card", True); lb.setAlignment(QtCore.Qt.AlignCenter)
            lb.setWordWrap(True); lb.setMinimumHeight(52); cards.addWidget(lb, 1); self.dash_cards.append(lb)
        lay.addLayout(cards)
        self.dash_stamp = UI.QLabel(UI.tr('No successful refresh yet')); self.dash_stamp.setProperty("caption", True); lay.addWidget(self.dash_stamp)
        self.dash_tabs = QtWidgets.QTabBar(); self.dash_tabs.setExpanding(False); self.dash_tabs.setDrawBase(False)
        self.dash_tabs.currentChanged.connect(lambda _: self._dash_fill()); lay.addWidget(self.dash_tabs)
        self.dash_tree = self._table(["Task", "Type", "Status", "Progress", "Ranking", "Updated"])
        self.dash_tree.setShowGrid(False); self.dash_tree.setAlternatingRowColors(True)
        self.dash_tree.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.dash_tree.verticalHeader().setDefaultSectionSize(36)
        header = self.dash_tree.horizontalHeader(); header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QtWidgets.QHeaderView.Stretch)
        for col, width in [(1, 110), (2, 150), (3, 100), (4, 100), (5, 170)]: header.resizeSection(col, width)
        self._dash_sort = (-1, 0); header.setSectionsClickable(True); header.sectionClicked.connect(self._dash_sort_clicked)
        self.dash_tree.setContextMenuPolicy(QtCore.Qt.CustomContextMenu); self.dash_tree.customContextMenuRequested.connect(self._dash_ctx)
        self.dash_tree.itemDoubleClicked.connect(lambda _: self._dash_view()); lay.addWidget(self.dash_tree, 1)
        row = QtWidgets.QHBoxLayout(); self._dash_actions = []
        for title, slot in [("Open results", self._dash_view), ("Details", self._dash_detail), ("Retry failed tasks", self._dash_retry), ('Continue inference', self._dash_continue)]:
            b = UI.QPushButton(title); b.clicked.connect(slot); row.addWidget(b); self._dash_actions.append(b)
        menu_button = UI.QPushButton(UI.tr('More')); menu = QtWidgets.QMenu(menu_button)
        for title, slot in [("Re-aggregate results", lambda: self._dash_update(self._dash_selected())),
                            ("Copy log directory", self._dash_logs), ("Rebuild resubmit command", self._dash_rebuild),
                            ("Fill into editor", self._dash_prefill), ("Clean batch data…", lambda: self._clean_flow(self._dash_selected()))]:
            act = UI.action(menu, title); act.triggered.connect(slot)
        menu_button.setMenu(menu); row.addWidget(menu_button); self._dash_actions.append(menu_button); row.addStretch(); lay.addLayout(row)
        self.dash_det = self._out(); self.dash_details_panel = UI.OutputPanel(self.dash_det, title="Task details")
        lay.addWidget(self.dash_details_panel)
        self._dash_timer = QtCore.QTimer(self); self._dash_timer.timeout.connect(self._dash_refresh)
        self.dash_tree.itemSelectionChanged.connect(self._dash_actions_state)
        self._dash_actions_state()
        QtCore.QTimer.singleShot(500, self._dash_refresh)

    @staticmethod
    def _pairs_cell(b):
        """pairs 列:pairs_total 未知显示 '-';done 未知(还在跑)显示 '?/N'。"""
        if b.get("pairs_total") is None:
            return "-"
        d = b.get("pairs_done")
        return f"{d if d is not None else '?'}/{b['pairs_total']}"

    def _table(self, headers):
        t = QtWidgets.QTableWidget(0, len(headers))
        t.setHorizontalHeaderLabels(headers)
        # 列宽用户可手调(有的表头/内容较长),末列吃掉剩余空间
        t.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.Interactive)
        t.horizontalHeader().setStretchLastSection(True)
        t.horizontalHeader().setDefaultAlignment(QtCore.Qt.AlignLeft|QtCore.Qt.AlignVCenter)
        t.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        t.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        t.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        t.verticalHeader().setVisible(False)
        self._enable_table_copy(t)
        return t

    def _enable_table_copy(self, t):
        """(#8)表格文字可复制:Ctrl+C 复制当前单元格;右键菜单 Copy cell text。"""
        sc = QtGui.QShortcut(QtGui.QKeySequence.Copy, t)
        sc.setContext(QtCore.Qt.WidgetWithChildrenShortcut)
        sc.activated.connect(lambda: self._table_copy_current(t))

    def _table_copy_current(self, t):
        """Ctrl+C:单选复制当前单元格;多选行(ExtendedSelection 的表)
        复制为 TSV(每行一行、列间制表符,可直接粘进 Excel)。"""
        sel_rows = sorted({i.row() for i in t.selectedIndexes()})
        if len(sel_rows) > 1:
            lines = []
            for r in sel_rows:
                cells = []
                for c in range(t.columnCount()):
                    it = t.item(r, c)
                    cells.append(it.text() if it else "")
                lines.append("\t".join(cells).rstrip())
            self._copy("\n".join(lines), f"{len(sel_rows)} rows")
            return
        it = t.currentItem()
        if it:
            self._copy(it.text(), "Cell text")

    def _table_copy_menu(self, t, pos):
        it = t.itemAt(pos)
        if not it:
            return None
        m = QtWidgets.QMenu(self)
        act = UI.action(m, UI.tr('Copy cell text'))
        if m.exec(t.viewport().mapToGlobal(pos)) is act:
            self._copy(it.text(), "Cell text")
        return act

    def _fill_table(self, t, rows):
        t.setRowCount(0); t.setRowCount(len(rows))
        for i, row in enumerate(rows):
            for j, cell in enumerate(row):
                item = QtWidgets.QTableWidgetItem()
                if isinstance(cell, UI.Message): UI.bind(item, 'setText', cell)
                else: item.setText(str(cell))
                t.setItem(i, j, item)
        t.resizeColumnsToContents()   # 给合理初始宽,之后用户可手调

    def _dash_ctx(self, pos):
        """批次表右键菜单:复制单元格 / 批次目录 / spec 的绝对路径。
        batch dict 存在该行第 0 列 item 的 UserRole 里。"""
        it = self.dash_tree.itemAt(pos)
        if not it:
            return
        m = QtWidgets.QMenu(self)
        act_cell = UI.action(m, UI.tr('Copy cell text'))
        act_rows = None
        if len({i.row() for i in self.dash_tree.selectedIndexes()}) > 1:
            act_rows = UI.action(m, UI.tr('Copy selected rows (TSV)'))
        it0 = self.dash_tree.item(it.row(), 0)
        b = it0.data(QtCore.Qt.UserRole) if it0 else None
        act_dir = act_spec = None
        if b:
            act_dir = UI.action(m, UI.tr('Copy absolute path'))
            act_spec = UI.action(m, UI.tr('Copy spec.json path')) if b.get("spec") else None
        hit = m.exec(self.dash_tree.viewport().mapToGlobal(pos))
        if hit is act_cell:
            self._copy(it.text(), "Cell text")
        elif act_rows is not None and hit is act_rows:
            self._table_copy_current(self.dash_tree)
        elif b and hit is act_dir:
            self._copy(b["dir"])
        elif b and act_spec is not None and hit is act_spec:
            self._copy(b["spec"])

    def _dash_auto(self, on):
        (self._dash_timer.start(30000) if on else self._dash_timer.stop())

    def _dash_refresh(self):
        if self._dash_inflight: return
        self._dash_inflight = True
        self._busy([self.dash_btn], True)

        def target():
            # Local files remain usable even when Slurm is unavailable.
            ov = batches = None
            errors = []
            try: batches = B.scan_batches()
            except Exception as exc: errors.append(("Local task list could not be read", str(exc)))
            try:
                rc, out, err = B.run_af3(["status", "--json"], timeout=30)
                if rc: raise RuntimeError(err or out or "squeue failed")
                ov = json.loads(out)
                if batches is not None and 'queue_jobs' in ov:
                    B.apply_queue_status(batches, ov['queue_jobs'])
            except Exception as exc: errors.append(("Queue status unavailable", str(exc)))
            return ov, batches, errors

        def done(err, result):
            self._dash_inflight = False
            self._busy([self.dash_btn], False)
            ov, batches, errors = result if not err else (None, None, [("Queue status unavailable", str(err))])
            self.dash_error.setVisible(bool(errors))
            UI.bind(self.dash_error_details, 'setPlainText', "\n\n".join(e[1] for e in errors))
            UI.bind(self.dash_error_toggle, 'setText', (UI.tr(errors[0][0]) if errors else UI.tr('')))
            if not errors:
                self.dash_error_toggle.setChecked(False)
            if ov is not None:
                self._queue_jobs = ov.get('queue_jobs')
                self._queue_jobs_time = time.time()
                self._queue_updated = time.strftime("%Y-%m-%d %H:%M:%S")
                sq = ov.get("squeue") or {}
                UI.bind(self.dash_cards[0], 'setText', UI.f("My jobs: {0}", ov.get("my_jobs", "?")))
                UI.bind(self.dash_cards[1], 'setText', UI.f("Running {0} · Pending {1}", sq.get("RUNNING", 0), sq.get("PENDING", 0)))
                UI.bind(self.dash_cards[2], 'setText', UI.f("MSA pool {0} · Output {1}", ov.get("msa_products", "?"), ov.get("output_dirs", "?")))
            stamp = getattr(self, "_queue_updated", None)
            UI.bind(self.dash_stamp, 'setText', (UI.f("Last successful refresh: {0}", stamp) if stamp else UI.tr("No successful refresh yet")))
            if batches is not None:
                self._dash_batches = batches
                UI.bind(self.dash_cards[3], 'setText', UI.f("Batches: {0}", len(batches)))
                self._dash_fill()
                self._res_batches = {B.batch_id(b): b for b in batches}
                self._res_fill_combo()
        enqueue(self._q, target, done)

    # 类型选项卡的固定顺序(熟悉的类型在前),其余按名字排在后
    _TYPE_ORDER = ("run", "pulldown", "scan", "seedtest")

    def _type_tabs(self, bar, batches):
        """重建类型选项卡(All + 各 type 带计数),尽量保留当前选中的类型。
        返回当前应生效的类型(None = All)。"""
        counts = {}
        for b in batches:
            t = b["type"] or "?"
            counts[t] = counts.get(t, 0) + 1
        order = [t for t in self._TYPE_ORDER if t in counts]
        order += sorted(t for t in counts if t not in self._TYPE_ORDER)
        cur = bar.tabData(bar.currentIndex())
        bar.blockSignals(True)
        while bar.count():
            bar.removeTab(0)
        bar.addTab(UI.render(UI.tr("All")) + f" ({len(batches)})")
        bar.setTabData(0, None)
        for t in order:
            bar.addTab(f"{UI.kind(t)} ({counts[t]})")
            bar.setTabData(bar.count() - 1, t)
        idx = next((i for i in range(bar.count()) if bar.tabData(i) == cur), 0)
        bar.setCurrentIndex(idx)
        bar.blockSignals(False)
        return bar.tabData(idx)

    def _dash_sort_clicked(self, col):
        """(#15)三态排序:同列再点循环 升 -> 降 -> 取消;换列从升序开始。"""
        if col >= self.dash_tree.columnCount():
            return
        c, o = self._dash_sort
        o = (o + 1) % 3 if c == col else 1
        self._dash_sort = (col, o)
        hdr = self.dash_tree.horizontalHeader()
        hdr.setSortIndicatorShown(o != 0)
        if o:
            hdr.setSortIndicator(
                col, QtCore.Qt.AscendingOrder if o == 1 else QtCore.Qt.DescendingOrder)
        self._dash_fill()

    @staticmethod
    def _dash_sort_key(b, col):
        """批次 dict -> 排序键(数值列按数值,空值排前)。"""
        if col == 0:
            return str(b["name"]).lower()
        if col == 1:
            return str(b["type"] or "")
        if col == 2:
            return str(b["status"] or "")
        if col == 3:
            return (-1 if b["pairs_done"] is None else b["pairs_done"],
                    -1 if b["pairs_total"] is None else b["pairs_total"])
        if col == 4:
            return 1 if b["has_ranking"] else 0
        if col == 5:
            return b["mtime"] or 0
        return ""

    def _dash_fill(self):
        if not hasattr(self, "dash_tree"): return
        batches = getattr(self, "_dash_batches", [])
        self.dash_cards[3].setText(UI.f("Batches: {0}",len(batches)))
        name_counts = {}
        for b in batches:
            name = B.batch_display_name(b); name_counts[name] = name_counts.get(name,0)+1
        sel_type = self._type_tabs(self.dash_tabs, batches)
        ft = self.dash_filter.text().strip().casefold()
        rows = [b for b in batches if (sel_type is None or b.get("type") == sel_type)
                and (not ft or ft in " ".join([B.batch_display_name(b), b["name"], b["dir"], UI.status(b.get("status", "")), UI.kind(b.get("type", ""))]).casefold())]
        col, order = getattr(self, "_dash_sort", (-1, 0))
        if order: rows.sort(key=lambda b: self._dash_sort_key(b, col), reverse=order == 2)
        t = self.dash_tree
        selected = {B.batch_id(t.item(i.row(), 0).data(QtCore.Qt.UserRole)) for i in t.selectionModel().selectedRows() if t.item(i.row(), 0)}
        t.blockSignals(True)
        t.clearSelection()
        t.setCurrentCell(-1, -1)
        t.setRowCount(len(rows))
        t.setHorizontalHeaderLabels([UI.render(UI.tr(k)) for k in ("Task", "Type", "Status", "Progress", "Ranking", "Updated")])
        for i, b in enumerate(rows):
            display = B.batch_display_name(b)
            if name_counts.get(display,0)>1:
                display += " · " + UI.render(UI.tr("MSA / inference pool" if b.get("source")=="infer_pool" else "Output")) + " · " + b.get("short_id",B.R.digest(B.batch_id(b),8))
            cells = [display, UI.kind(b.get("type", "")), UI.status(b.get("status", "")), self._pairs_cell(b),
                     UI.render(UI.tr("Ready")) if b.get("has_ranking") else "—", B.fmt_mtime(b.get("mtime", 0))]
            for j, value in enumerate(cells):
                item = QtWidgets.QTableWidgetItem(str(value))
                if j == 0:
                    item.setData(QtCore.Qt.UserRole, b)
                    UI.bind(item, 'setToolTip', b["dir"])
                if j == 2:
                    status=b.get('status','')
                    color = "error" if "fail" in status else "warning" if status in ('legacy_controller','scheduler_unavailable','controller_stopped') else "success" if status in ("done", "succeeded") else "body"
                    item.setForeground(QtGui.QColor(CC[color]))
                t.setItem(i, j, item)
            if B.batch_id(b) in selected:
                t.selectionModel().setCurrentIndex(t.model().index(i, 0), QtCore.QItemSelectionModel.ClearAndSelect | QtCore.QItemSelectionModel.Rows)
        t.blockSignals(False)
        self._dash_actions_state()

    def _dash_actions_state(self):
        selected = self._dash_selected()
        for control in getattr(self, "_dash_actions", []): control.setEnabled(bool(selected))
        if not selected:
            self._detail_generation += 1
            self._dash_detail_batch = None
            self.dash_det.clear()

    def _dash_update(self, b):
        r = UI.QMessageBox.question(self, UI.tr('Update results'), UI.f('Re-aggregate results for batch:\n  {0}\n\nThis updates ranking.csv / iptm_matrix.csv (+ scan reports), preserves result paths, and regenerates results_report.html.\nIt does NOT rerun msa / infer. Continue?', b['name']))
        if r != QtWidgets.QMessageBox.Yes:
            return
        self.statusBar().showMessage(UI.render(UI.f('refreshing {0}…', b['name'])), 3000)

        def target():
            rc, out, serr = B.run_af3(["refresh", b["dir"]], timeout=1800)
            hp = None
            if rc == 0:
                hp, _ = B.export_batch_html(b["dir"], names=None)
            return rc, out, serr, hp

        def done(err, res):
            if err:
                UI.QMessageBox.critical(self, UI.tr('Update failed'), str(err)); return
            rc, out, serr, hp = res
            if rc != 0:
                UI.QMessageBox.critical(self, UI.tr('Update failed'), (serr or out or "refresh failed")[:1200])
                return
            self.statusBar().showMessage(UI.render(UI.f("{0}{1}", UI.f('{0}: results updated', b['name']), (UI.f('; HTML: {0}', hp) if hp else UI.tr('')))), 6000)
            self._dash_refresh()
            self._res_refresh()
        enqueue(self._q, target, done)

    def _dash_selected(self):
        """当前选中批次的 dict(存在该行第 0 列 item 的 UserRole 里)。"""
        rows = self.dash_tree.selectionModel().selectedRows()
        if len(rows) != 1:
            return None
        r = rows[0].row()
        it = self.dash_tree.item(r, 0)
        return it.data(QtCore.Qt.UserRole) if it else None

    def _dash_detail(self):
        b=self._dash_selected()
        self._detail_generation+=1;generation=self._detail_generation
        if not b:return
        self._dash_detail_batch = b
        UI.bind(self.dash_det, 'setPlainText', UI.tr('正在读取任务摘要…'))
        def done(err,result):
            if generation!=self._detail_generation:return
            UI.bind(self.dash_det, 'setPlainText', (str(err) if err else result))
        enqueue(self._q,lambda:B.batch_summary(b, self._lang),done)

    def _dash_retry(self):
        b=self._dash_selected()
        if not b or not b.get("spec"):
            self.statusBar().showMessage(UI.render(UI.tr('请选择有任务计划的批次')), 4000);return
        self._exec(["retry","--spec",b["spec"]],False,[],self.dash_det)

    def _dash_continue(self):
        b = self._dash_selected()
        if b and b.get('spec'):
            self._exec(['continue','--spec',b['spec']],False,[],self.dash_det)

    def _dash_logs(self):
        b=self._dash_selected()
        if b:self._copy(os.path.join(b["dir"],"logs"),"日志目录")

    def _dash_view(self):
        b = self._dash_selected()
        if not b: return
        identity = B.batch_id(b)
        self._res_batches[identity] = b
        self._res_select(identity)
        self.tabs.setCurrentWidget(self._results_tab)

    def _dash_rebuild(self):
        """从 spec.json 重建提交命令(原 History 页的一键重提;--force 只补未完成)。"""
        b = self._dash_selected()
        if not b:
            UI.QMessageBox.information(self, UI.tr('Select'), UI.tr('Please select a batch first.'))
            return
        if not b["spec"]:
            UI.QMessageBox.information(self, UI.tr('No spec'), UI.tr('This batch has no spec.json.'))
            return
        try:
            spec = B.R.read_json(b['spec'])
        except Exception as e:
            UI.QMessageBox.critical(self, UI.tr('spec read failed'), str(e)); return
        args, note = B.rebuild_args_from_spec(spec)
        if args is None:
            UI.QMessageBox.critical(self, UI.tr('Cannot rebuild'), note or ""); return
        UI.bind(self.dash_det, 'setPlainText', UI.f("{0}{1}", UI.f("{0}{1}", note, UI.tr('\n\n$ ')), B.cmdline_str(args)))
        r = UI.QMessageBox.question(self, UI.tr('Confirm resubmit'), UI.f('{0}\n\nCommand:\n{1}\n\nRun it?', note, B.cmdline_str(args)), QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No)
        if r == QtWidgets.QMessageBox.Yes:
            self._exec(args, False, [self.dash_rebuild], self.dash_det)

    def _dash_prefill(self):
        """选中批次 -> 把初始输入填回对应提交页(改完再提交)。
        数据源同 _dash_rebuild:优先 spec["input"](初始输入),
        旧批次回退为 pairs 反推(scan 是片段级近似,给警告)。"""
        b = self._dash_selected()
        if not b:
            UI.QMessageBox.information(self, UI.tr('Select'), UI.tr('Please select a batch first.'))
            return
        if not b["spec"]:
            UI.QMessageBox.information(self, UI.tr('No spec'), UI.tr('This batch has no spec.json.'))
            return
        try:
            spec = B.R.read_json(b['spec'])
        except Exception as e:
            UI.QMessageBox.critical(self, UI.tr('spec read failed'), str(e)); return
        a_lines, b_lines, info = B.spec_prefill(spec)
        if a_lines is None:
            UI.QMessageBox.critical(self, UI.tr('Cannot prefill'), (info or {}).get("error", "")); return
        t = info.get("type")
        if t == "run":
            UI.bind(self.sub_expr, 'setPlainText', "\n".join(a_lines))
            UI.bind(self.sub_name, 'setText', info.get("name") or "")
            self.tabs.setCurrentWidget(self._submit_tab)
        elif t in ("pulldown", "scan"):
            tab = self.pd if t == "pulldown" else self.sc
            UI.bind(tab.a, 'setPlainText', "\n".join(a_lines))
            UI.bind(tab.b, 'setPlainText', "\n".join(b_lines))
            UI.bind(tab.name, 'setText', info.get("name") or "")
            if t == "scan":
                sp = info.get("scan_params") or {}
                if sp.get("mode") == "pae":
                    tab.mode_pae.setChecked(True)
                else:
                    tab.mode_win.setChecked(True)
                if sp.get("win"):
                    tab.win.setValue(sp["win"])
                if sp.get("overlap"):
                    tab.ov.setValue(sp["overlap"])
                if sp.get("min_frag"):
                    tab.mf.setValue(sp["min_frag"])
                if sp.get("split_threshold"):
                    tab.th.setValue(sp["split_threshold"])
                for sk, w in (("pae_cutoff", tab.pae_cut),
                              ("pae_resolution", tab.pae_res),
                              ("pae_min_domain", tab.pae_mdom),
                              ("pae_domains_per_window", tab.pae_dpw),
                              ("pae_min_frag", tab.pae_mf),
                              ("pae_max_frag", tab.pae_maxf)):
                    if sp.get(sk) is not None:
                        w.setValue(sp[sk])
            self.tabs.setCurrentWidget(self._pd_tab if t == "pulldown" else self._sc_tab)
        else:
            UI.QMessageBox.information(self, UI.tr('Not supported'), UI.f("Batch type '{0}' cannot be prefilled.", t))
            return
        if not info.get("from_input"):
            self.statusBar().showMessage(UI.render(UI.f("{0}{1}", UI.f("{0}{1}", UI.f('{0}: old batch without recorded original input — prefilled from processed pairs', b['name']), (UI.tr(' (fragment-level approximation for scan)') if t == "scan" else UI.tr(''))), UI.tr('; please review before submitting.'))), 12000)
        else:
            self.statusBar().showMessage(UI.render(UI.f('{0}: original input filled into the {1} page.', b['name'], t)), 8000)

    # ================================================================ Results
    def _build_results(self):
        w = QtWidgets.QWidget(); self._results_tab = self._wrap_scroll(w); UI.tab(self.tabs, self._results_tab, UI.tr('Results'))
        lay = QtWidgets.QVBoxLayout(w); lay.setContentsMargins(20, 16, 20, 16); lay.setSpacing(12)
        heading = self._intro("results")
        lay.addLayout(heading)
        self._res_batches = {}; self._res_selected_id = self._settings.value("result_task_id", None)
        bar = QtWidgets.QHBoxLayout()
        self.res_current = UI.QLabel(UI.tr('Select a task to view its results.')); self.res_current.setWordWrap(True)
        bar.addWidget(self.res_current, 1)
        self.res_switch = UI.QPushButton(UI.tr('Switch task')); self.res_switch.clicked.connect(self._res_choose)
        self.res_btn = UI.QPushButton(UI.tr('Refresh list')); self.res_btn.clicked.connect(self._res_refresh)
        heading.insertSpacing(heading.count()-1, 16)
        for button in (self.res_switch, self.res_btn):
            button.setObjectName('darkAction')
            heading.insertWidget(heading.count()-1, button)
        lay.addLayout(bar)
        details = QtWidgets.QWidget(); form = QtWidgets.QHBoxLayout(details); form.setContentsMargins(0,0,0,0)
        self.res_path = UI.QLineEdit(); self.res_path.setReadOnly(True)
        self.res_id = UI.QLineEdit(); self.res_id.setReadOnly(True)
        self.res_id.setMaximumWidth(230); self.res_id.setMinimumWidth(90)
        copy = UI.QPushButton(UI.tr('Copy path')); copy.clicked.connect(lambda: self._copy(self.res_path.text()))
        form.addWidget(UI.QLabel(UI.tr('Identifier'))); form.addWidget(self.res_id)
        form.addWidget(UI.QLabel(UI.tr('Directory'))); form.addWidget(self.res_path,1); form.addWidget(copy)
        lay.addWidget(details)
        self.res_label = UI.QLabel(); self.res_label.setWordWrap(True); self.res_label.setProperty("caption", True); lay.addWidget(self.res_label)
        self.res_body = QtWidgets.QWidget(); self.res_body_l = QtWidgets.QVBoxLayout(self.res_body); self.res_body_l.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.res_body, 1)
        # Dashboard's startup scan populates both views.

    def _res_choose(self):
        dialog = UI.BatchPicker(list(self._res_batches.values()), self._res_selected_id, self)
        if dialog.exec() == QtWidgets.QDialog.Accepted:
            self._res_select(dialog.selected_id)
        dialog.deleteLater()

    def _res_select(self, identity):
        self._res_selected_id = identity
        self._settings.setValue("result_task_id", identity or "")
        self._res_fill_combo()

    def _res_refresh(self):
        if self._res_inflight:return
        self._res_inflight=True
        def target():
            return B.scan_batches()

        def done(err, batches):
            self._res_inflight=False
            if err:
                UI.QMessageBox.critical(self, UI.tr('Refresh failed'), str(err)); return
            queue=getattr(self,'_queue_jobs',None)
            if queue is not None and time.time()-getattr(self,'_queue_jobs_time',0)<90:
                B.apply_queue_status(batches,queue)
            self._res_batches = {B.batch_id(b): b for b in batches}
            self._res_fill_combo()
            # 反向联动:Results 刷新也让 Dashboard 表用这份新列表
            # (内容没变就跳过,避免无谓重建表格)
            if getattr(self, "_dash_batches", None) != batches:
                self._dash_batches = batches
                self._dash_fill()
        enqueue(self._q, target, done)

    def _res_fill_combo(self):
        # Kept as an internal refresh hook; selection no longer uses a combo box.
        if not hasattr(self, "res_current"): return
        b = self._res_batches.get(self._res_selected_id)
        if not b:
            b = next((row for row in self._res_batches.values() if self._res_selected_id in
                      [os.path.normcase(os.path.abspath(p)) for p in row.get('pool_aliases',[])]),None)
            if b:
                self._res_selected_id=B.batch_id(b)
                self._settings.setValue('result_task_id',self._res_selected_id)
        if b:
            UI.bind(self.res_current, 'setText', UI.percent('%s  ·  %s  ·  %s', (B.batch_display_name(b), UI.kind(b.get("type", "")), UI.status(b.get("status", "")))))
            UI.bind(self.res_path, 'setText', b["dir"]); UI.bind(self.res_id, 'setText', b.get("internal_name", b["name"]))
            self.res_path.setToolTip(b['dir']); self.res_id.setToolTip(b.get('internal_name',b['name']))
        else:
            self.res_path.clear(); self.res_id.clear()
            UI.bind(self.res_current, 'setText', (UI.tr("The selected task is no longer in the list. Choose another task.") if self._res_selected_id else UI.tr("Select a task to view its results.")))
        self._res_render()

    def _export_html(self, outdir, quiet=False):
        def done(err, res):
            path, perr = res if res else (None, None)
            if err or perr:
                if not quiet:
                    UI.QMessageBox.critical(self, UI.tr('Export failed'), str(err or perr))
                return
            if not quiet:
                self._copy(os.path.dirname(path), "Folder path")
                UI.QMessageBox.information(self, UI.tr('HTML report written'), UI.f('{0}\n\nFolder path copied — paste into MobaXterm SFTP panel and drag results_report.html to your laptop.', path))
            cur = getattr(self, "_res_batches", {}).get(self._res_selected_id)
            if cur and cur["dir"] == outdir:
                self._res_render()
        enqueue(self._q, lambda: B.export_batch_html(outdir, names=self._names), done)

    def _clear(self, layout):
        while layout.count():
            item = layout.takeAt(0)
            if item.widget():
                item.widget().hide()
                item.widget().deleteLater()

    def _res_render(self):
        b=getattr(self,"_res_batches",{}).get(self._res_selected_id)
        key=(B.batch_id(b),B.result_version(b.get('result_dir') or b['dir']),HAS_MPL) if b else None
        if key is not None and key==self._render_key:return
        restore={}
        if key and self._render_key and key[0]==self._render_key[0]:
            nb=getattr(self,'_res_nb',None)
            if nb and isValid(nb):restore['tab']=nb.currentIndex()
            restore['params']={name:getattr(self,name).value() for name in ('pd_cut','pd_res','pd_mdom','pd_dpw','pd_mf','pd_maxf') if hasattr(self,name) and isValid(getattr(self,name))}
            restore['last']=getattr(self,'_pae_last',None)
            restore['domains']=getattr(self,'_pae_dom_results',{})
            restore['file_version']=getattr(self,'_pae_file_version',None)
        self._res_restore=restore
        self._cancel_pae_work()
        self._render_key=key;self._render_generation+=1;generation=self._render_generation
        self._pae_token+=1;self._pae_last=None
        self._clear_pae_domain_controls()
        self._pae_dom_results={}
        self._clear(self.res_body_l)
        if not b:
            self.res_path.clear(); self.res_id.clear(); UI.bind(self.res_label, 'setText', (UI.tr("No tasks yet. Submit a prediction, then refresh the list.") if not self._res_batches else UI.tr(''))); return
        outdir=b.get('result_dir') or b["dir"];UI.bind(self.res_path, 'setText', outdir)
        UI.bind(self.res_label, 'setText', UI.tr("Reading results index…"))
        def target():
            paths=B.result_paths(outdir)
            available={k:os.path.isfile(v) for k,v in paths.items() if isinstance(v,str)}
            defaults=B.R.spec_summary(b.get('spec') or '').get('scan_params',{})
            return paths,available,defaults
        def done(err,result):
            if generation!=self._render_generation:return
            if err:UI.bind(self.res_label, 'setText', UI.f("{0}{1}", UI.tr('读取失败: '), str(err)));return
            paths,available,defaults=result
            self._loaded_struct_entries=None
            self._loaded_scan_params=defaults
            UI.bind(self.res_label, 'setText', (UI.tr("Interface score: mean chain-pair ipTM across A/B.") if any(available.values()) else UI.tr("No summary tables yet. Check PAE viewer for available models.")))
            nb=QtWidgets.QTabWidget();self.res_body_l.addWidget(nb);self._res_nb=nb
            nb.tabBar().installEventFilter(self)
            factories={}
            def add(title,factory,structure=False):
                holder=QtWidgets.QWidget();QtWidgets.QVBoxLayout(holder)
                index=UI.tab(nb,holder,title);factories[index]=(holder,factory,structure)
                if structure:self._struct_tab=holder
            if available.get("ranking"):add("Interface ranking",lambda:self._res_table_tab(paths["ranking"],"ranking.csv",view_pae=True))
            if available.get("matrix"):add("Interface matrix",lambda:self._res_plot_tab(paths["matrix"],"matrix","iptm_matrix.csv",paths.get("ranking")))
            if available.get("hits"):add("Hit summary",lambda:self._res_table_tab(paths["hits"],"scan_hits.csv"))
            if available.get("profile"):add("Fragment overview",lambda:self._res_plot_tab(paths["profile"],"profile","iptm_profile.csv"))
            if available.get("prot_matrix"):add("Protein matrix",lambda:self._res_plot_tab(paths["prot_matrix"],"matrix","iptm_matrix_protein.csv"))
            add("PAE viewer",lambda:self._res_struct_tab(outdir),True)
            def load(index):
                pending=factories.pop(index,None)
                if pending:
                    holder,factory,structure=pending
                    holder.layout().addWidget(factory())
            nb.currentChanged.connect(load)
            nb.setCurrentIndex(min(restore.get('tab',0),nb.count()-1));load(nb.currentIndex())
        enqueue(self._q,target,done)

    def _res_table_tab(self,path,dl,view_pae=False):
        w=QtWidgets.QWidget();lay=QtWidgets.QVBoxLayout(w)
        t=self._table([]);lay.addWidget(t,1)
        controls=QtWidgets.QHBoxLayout();previous=UI.QPushButton(UI.tr('上一页'));next_page=UI.QPushButton(UI.tr('下一页'));label=UI.QLabel()
        controls.addWidget(previous);controls.addWidget(label);controls.addWidget(next_page)
        copy_button=UI.QPushButton(UI.tr('复制选中目录'));controls.addWidget(copy_button)
        if view_pae:
            view=UI.QPushButton(UI.tr('查看选中 PAE'));view.clicked.connect(lambda:self._jump_to_pae(t));controls.addWidget(view)
        lay.addLayout(controls);lay.addLayout(self._dl_row(path,dl))
        search=UI.QLineEdit();search.setPlaceholderText(UI.tr('Search all result rows'))
        lay.insertWidget(0,search)
        state={"page":0,"generation":0,"columns":[],"sort":None,"descending":False}
        def load(page):
            state["generation"]+=1;token=state["generation"];previous.setEnabled(False);next_page.setEnabled(False);UI.bind(label, 'setText', UI.tr('读取中…'))
            def done(err,result):
                if not isValid(w) or token!=state["generation"]:return
                if err:UI.bind(label, 'setText', str(err));return
                cols,rows,total=result;state["page"]=page;state["columns"]=cols
                t.setSortingEnabled(False);t.clear();t.setColumnCount(len(cols));t.setHorizontalHeaderLabels(cols)
                t.setRowCount(len(rows))
                for i,row in enumerate(rows):
                    for j,value in enumerate(row[:len(cols)]):
                        item=QtWidgets.QTableWidgetItem(value)
                        try:item.setData(QtCore.Qt.DisplayRole,float(value))
                        except ValueError:pass
                        t.setItem(i,j,item)
                t.setSortingEnabled(False);t.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.Interactive)
                UI.bind(label, 'setText', UI.f('{0}–{1} / {2} (all results)',page*200+1 if total else 0,min((page+1)*200,total),total))
                previous.setEnabled(page>0);next_page.setEnabled((page+1)*200<total)
            query=search.text();column=state['sort'];descending=state['descending']
            enqueue(self._q,lambda:B.csv_page(path,page,200,query,column,descending),done)
        def sort(column):
            state['descending']=not state['descending'] if state['sort']==column else False
            state['sort']=column;load(0)
        t.horizontalHeader().sectionClicked.connect(sort)
        delay=QtCore.QTimer(w);delay.setSingleShot(True);delay.setInterval(250)
        search.textChanged.connect(lambda:delay.start());delay.timeout.connect(lambda:load(0))
        previous.clicked.connect(lambda:load(max(0,state["page"]-1)));next_page.clicked.connect(lambda:load(state["page"]+1))
        def copy_folder():
            col=next((state["columns"].index(k) for k in ("folder","best_folder","dir") if k in state["columns"]),None)
            if col is not None and t.currentRow()>=0:
                item=t.item(t.currentRow(),col)
                if item:self._copy(os.path.abspath(os.path.join(os.path.dirname(path),item.text())))
        copy_button.clicked.connect(copy_folder);load(0)
        return w

    def _res_plot_tab(self,path,kind,dl,ranking_path=None):
        w=QtWidgets.QWidget();lay=QtWidgets.QVBoxLayout(w);note=UI.QLabel(UI.tr('正在后台绘图…'));lay.addWidget(note)
        lay.addLayout(self._dl_row(path,dl))
        def done(err,result):
            if not isValid(w):return
            if err:UI.bind(note, 'setText', UI.f("{0}{1}", UI.tr('绘图失败: '), str(err)));return
            png,values,rows,cols,pos=result
            pix=QtGui.QPixmap();pix.loadFromData(png,"PNG")
            note.hide();lay.insertWidget(0,HeatmapLabel(pix,values,rows,cols,pos) if values is not None else HeatmapLabel(pix),1)
        enqueue(self._q,lambda:B.render_plot(path,kind),done)
        return w

    def _jump_to_pae(self, table):
        rows=table.selectionModel().selectedRows()
        if len(rows)!=1:return
        columns=[table.horizontalHeaderItem(i).text() for i in range(table.columnCount())]
        if 'folder' not in columns:return
        item=table.item(rows[0].row(),columns.index('folder'))
        if not item:return
        batch=self._res_batches.get(self._res_selected_id)
        if not batch:return
        folder=os.path.normcase(os.path.abspath(os.path.join(batch.get('result_dir') or batch['dir'],item.text())))
        self._pending_struct_folder=folder
        self._res_nb.setCurrentWidget(self._struct_tab)
        self._select_pending_struct()

    def _select_pending_struct(self):
        folder=getattr(self,'_pending_struct_folder',None)
        if not folder or getattr(self,'_loaded_struct_entries',None) is None:return
        self._pending_struct_folder=None
        for i,entry in enumerate(getattr(self,'_struct_entries',[])):
            if os.path.normcase(os.path.abspath(os.path.dirname(entry['path'])))==folder:
                self._struct_cb.setCurrentIndex(i);self._struct_select();return
        self.statusBar().showMessage(UI.render(UI.tr('The selected result has no model yet.')),4000)

    def _res_struct_tab(self, outdir):
        """PAE viewer:顶部下拉选模型,其下 [Select] / [Copy folder path];
        点 Select 后:右侧出 PAE(loading 文案 + 线性进度条),左下出该模型的
        summary confidences(易读表格,不是裸 json)。不自动加载,避免误触。"""
        w = QtWidgets.QWidget(); lay = QtWidgets.QVBoxLayout(w)
        entries = getattr(self,"_loaded_struct_entries",None)
        if entries is None:
            loading = UI.QLabel(UI.tr('Reading results index…')); lay.addWidget(loading)
            generation=self._render_generation
            def loaded(err,result):
                if generation!=self._render_generation or not isValid(w):return
                if err: UI.bind(loading,'setText',str(err));return
                self._loaded_struct_entries=result
                self._clear(lay);lay.addWidget(self._res_struct_tab(outdir))
            enqueue(self._q,lambda:B.list_ranked_cifs(outdir,cap=100000),loaded)
            return w
        self._struct_entries = entries
        self._pae_token += 1
        self._pae_last = None
        if not entries:
            lay.addWidget(UI.QLabel(UI.tr('No *_model.cif yet (job not finished).')))
            return w
        lay.addWidget(self._hint_label("struct_hint"))
        # (#5)下拉里也给 accession 标注蛋白名
        def anno_side(s):
            acc, _ = B.split_acc_label(s)
            nm = self._names.get(acc) if acc else None
            return f"{s} · {nm}" if nm else s
        labels = []
        for e in entries:
            lab = e["label"]
            if e["a"] or e["b"]:
                lab = lab.replace(e["a"] + "+" + e["b"],
                                  anno_side(e["a"]) + "+" + anno_side(e["b"]))
            labels.append(lab)
        split = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        lay.addWidget(split, 1)
        left = QtWidgets.QWidget(); ll = QtWidgets.QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 8, 0)
        self._struct_cb = QtWidgets.QComboBox()
        cb = self._struct_cb
        cb.setMaxVisibleItems(10)
        cb.setStyleSheet('QComboBox { combobox-popup: 0; }')
        cb.addItems(labels); cb.setMinimumWidth(260)
        cb.setEditable(True)                       # 输入即过滤长列表(不新增条目)
        cb.setInsertPolicy(QtWidgets.QComboBox.NoInsert)
        if cb.completer():
            cb.completer().setFilterMode(QtCore.Qt.MatchContains)
            cb.completer().setCompletionMode(QtWidgets.QCompleter.PopupCompletion)
            cb.completer().setMaxVisibleItems(10)
        ll.addWidget(cb)
        paths = [e["path"] for e in entries]

        def cur():
            i = cb.currentIndex()
            return paths[i] if 0 <= i < len(paths) else None
        brow = QtWidgets.QHBoxLayout()
        self._struct_sel = UI.QPushButton(UI.tr('Select'))
        self._struct_sel.setObjectName("primary")
        UI.bind(self._struct_sel, 'setToolTip', UI.tr("load this model's PAE plot and confidence summary"))
        if qta:
            self._struct_sel.setIcon(self._ic("fa5s.eye", "#ffffff"))
        self._struct_sel.clicked.connect(self._struct_select)
        brow.addWidget(self._struct_sel)
        bcp = UI.QPushButton(UI.tr('Copy folder path'))
        UI.bind(bcp, 'setToolTip', UI.tr('Copy the CONTAINING FOLDER path (paste into MobaXterm SFTP panel address bar)'))
        bcp.clicked.connect(lambda: cur() and self._copy(os.path.dirname(cur()), "Folder path"))
        brow.addWidget(bcp)
        self._pae_dom_btn = UI.QPushButton(UI.tr('PAE domains…'))
        UI.bind(self._pae_dom_btn, 'setToolTip', UI.tr("overlay PAE-domain fragment boundaries on the PAE plot; for complexes each protein chain's own diagonal block is segmented independently"))
        self._pae_dom_btn.setCheckable(True)
        self._pae_dom_btn.clicked.connect(self._toggle_pae_domains)
        brow.addWidget(self._pae_dom_btn)
        brow.addStretch(1)
        ll.addLayout(brow)

        # PAE domain 分段参数面板(默认隐藏;默认值取该批次 scan_params)
        self._pae_dom_panel = QtWidgets.QWidget()
        pg = QtWidgets.QGridLayout(self._pae_dom_panel)
        pg.setContentsMargins(0, 0, 0, 0)
        sp_defaults = getattr(self,'_loaded_scan_params',{})
        self.pd_cut = UI.QDoubleSpinBox()
        self.pd_cut.setRange(0.5, 30.0); self.pd_cut.setSingleStep(0.5)
        self.pd_cut.setDecimals(1)
        self.pd_cut.setValue(float(sp_defaults.get("pae_cutoff", 5.0)))
        self.pd_res = UI.QDoubleSpinBox()
        self.pd_res.setRange(0.05, 4.0); self.pd_res.setSingleStep(0.1)
        self.pd_res.setDecimals(2)
        self.pd_res.setValue(float(sp_defaults.get("pae_resolution", 0.5)))
        self.pd_mdom = self._spin(2, 200, int(sp_defaults.get("pae_min_domain", 10)))
        self.pd_dpw = self._spin(1, 10, int(sp_defaults.get("pae_domains_per_window", 1)))
        self.pd_mf = self._spin(10, 2000, int(sp_defaults.get("pae_min_frag", 250)))
        self.pd_maxf = self._spin(0, 20000, int(sp_defaults.get("pae_max_frag") or 0))
        self._pd_auto_maxf = int(sp_defaults.get("pae_max_frag")
                                 or sp_defaults.get("split_threshold") or 500)
        UI.bind(self.pd_maxf, 'setSpecialValueText', UI.f('auto (={0})', self._pd_auto_maxf))
        pd_items = (("cutoff", self.pd_cut), ("resolution", self.pd_res),
                    ("min-domain", self.pd_mdom), ("domains/window", self.pd_dpw),
                    ("min-frag", self.pd_mf), ("max-frag", self.pd_maxf))
        for i, (lab, spw) in enumerate(pd_items):
            r, c = divmod(i, 3)
            pg.addWidget(UI.QLabel(lab), r, c * 2)
            pg.addWidget(spw, r, c * 2 + 1)
        pd_apply = UI.QPushButton(UI.tr('Apply'))
        pd_apply.setObjectName("primary")
        UI.bind(pd_apply, 'setToolTip', UI.tr('recompute domains with these parameters and redraw'))
        pd_apply.clicked.connect(self._apply_pae_domains)
        pg.addWidget(pd_apply, 2, 0)
        cancel_domains=UI.QPushButton(UI.tr('Cancel analysis'))
        cancel_domains.clicked.connect(self._cancel_pae_work)
        pg.addWidget(cancel_domains,3,0)
        self._pae_dom_note = UI.QLabel(UI.tr(''))
        self._pae_dom_note.setWordWrap(True)
        self._pae_dom_note.setStyleSheet("color: gray")
        pg.addWidget(self._pae_dom_note, 2, 1, 1, 5)
        self._pae_dom_panel.setVisible(False)
        ll.addWidget(self._pae_dom_panel)
        cap = UI.QLabel(UI.tr('summary confidences'))
        cap.setProperty("caption", True)
        ll.addWidget(cap)
        self._conf_area = QtWidgets.QWidget()
        self._conf_l = QtWidgets.QVBoxLayout(self._conf_area)
        self._conf_l.setContentsMargins(0, 0, 0, 0)
        self._conf_l.addWidget(UI.QLabel(UI.tr('(press Select to load)')))
        ll.addWidget(self._conf_area, 1)
        split.addWidget(left)
        self.pae_area = QtWidgets.QWidget()
        self.pae_holder = QtWidgets.QVBoxLayout(self.pae_area)
        self.pae_holder.addWidget(self._hint_label("pae_idle"))
        split.addWidget(self.pae_area)
        split.setSizes([480, 820])
        QtCore.QTimer.singleShot(0,self._select_pending_struct)
        restore=getattr(self,'_res_restore',{})
        for name,value in restore.get('params',{}).items():getattr(self,name).setValue(value)
        last=restore.get('last')
        if isinstance(last,tuple) and restore.get('file_version')==B.R.file_version(last[5]):
            index=next((i for i,e in enumerate(entries) if B.confidences_json_for_cif(e['path'])==last[5]),None)
            if index is not None:
                cb.setCurrentIndex(index);self._pae_last=(*last[:3],index,*last[4:])
                self._show_conf_summary(entries[index]['path']);self._clear(self.pae_holder)
                self._pae_dom_results=restore.get('domains',{})
                if self._pae_dom_results:
                    self._rebuild_chain_buttons();self._redraw_dom_lines()
                else:self.pae_holder.addWidget(self._pae_canvas(last[0],last[1],ds=last[4]))
        return w

    def _struct_select(self):
        i = self._struct_cb.currentIndex()
        entries = getattr(self, "_struct_entries", [])
        if not (0 <= i < len(entries)):
            return
        cif = entries[i]["path"]
        self._show_conf_summary(cif)
        self._load_pae(i)

    def _show_conf_summary(self, cif):
        """左下:X_model.cif -> X_summary_confidences.json,易读指标表(不是裸 json)。"""
        self._clear(self._conf_l)
        # (#16)本函数同步新建 label(非队列回调,_drain 兜底扫不到):延迟补可选中
        QtCore.QTimer.singleShot(0, lambda: self._selectable_labels(self))
        base = cif[:-len("_model.cif")] if cif.endswith("_model.cif") else cif
        path = base + "_summary_confidences.json"
        if not os.path.isfile(path):
            self._conf_l.addWidget(UI.QLabel(UI.tr('no <name>_summary_confidences.json next to this cif (older batch?).')))
            return
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            self._conf_l.addWidget(UI.QLabel(UI.f('read failed: {0}', e)))
            return

        def fnum(v, pct=False):
            try:
                v = float(v)
            except (TypeError, ValueError):
                return None
            return f"{v * 100:.1f}%" if pct else f"{v:.3f}"
        rows = []
        for key, lab, pct in (("iptm", "ipTM", False), ("ptm", "pTM", False),
                              ("ranking_score", "ranking score", False),
                              ("fraction_disordered", "disordered fraction", True)):
            s = fnum(data.get(key), pct)
            if s is not None:
                rows.append((UI.tr(lab), s))
        if "has_clash" in data:
            rows.append((UI.tr("clash"), UI.tr("YES (check structure)" if data.get("has_clash") else "no")))
        for key, lab in (("chain_ptm", "pTM"),):     # chain ipTM 不展示(与整体 ipTM 重复感强)
            vals = data.get(key)
            if isinstance(vals, list):
                for idx, v in enumerate(vals):
                    s = fnum(v)
                    if s is not None:
                        rows.append((UI.f("chain {0} {1}", chr(65 + idx), lab), s))
        if not rows:
            self._conf_l.addWidget(UI.QLabel(UI.tr('(no recognizable metrics in summary json)')))
            return
        t = self._table(["metric", "value"])
        self._fill_table(t, rows)
        self._conf_l.addWidget(t)

    def _load_pae(self, i):
        """读该 cif 同目录的 <name>_confidences.json,右侧画 PAE(链边界+悬停)。
        期间显示 'loading PAE of x.cif' + 线性进度条(busy),完成后替换为图。"""
        entries = getattr(self, "_struct_entries", [])
        if not (0 <= i < len(entries)):
            return
        cif = entries[i]["path"]
        self._cancel_pae_work()
        cancellation = self._new_pae_request('load')
        name = os.path.basename(cif)
        conf = B.confidences_json_for_cif(cif)
        # 老版本 backend 没有这两个函数:降级(不画链边界),不崩
        _inp_fn = getattr(B, "input_json_for_cif", None)
        _chains_fn = getattr(B, "chains_from_input_json", None)
        inp = _inp_fn(cif) if _inp_fn else None
        self._pae_token += 1
        token = self._pae_token
        self._pae_last = None
        self._pae_dom_results = {}
        self._clear_pae_domain_controls()
        # 换了模型:上次 Apply 的 "computing…" 已过期,清掉防止看起来卡住
        if hasattr(self, "_pae_dom_note"):
            UI.bind(self._pae_dom_note, 'setText', UI.tr(''))
        self._clear(self.pae_holder)
        # (#16)同步新建的提示 label:_drain 兜底扫不到,延迟补可选中
        QtCore.QTimer.singleShot(0, lambda: self._selectable_labels(self))
        if not conf:
            self.pae_holder.addWidget(UI.QLabel(UI.tr('No <name>_confidences.json next to this cif.')))
            return
        lab = UI.QLabel(UI.f('loading PAE of {0} …', name))
        lab.setProperty("caption", True)
        bar = QtWidgets.QProgressBar()
        bar.setRange(0, 0)               # busy 模式:来回走的线性条
        bar.setTextVisible(False)
        self.pae_holder.addWidget(lab)
        self.pae_holder.addWidget(bar)
        self.pae_holder.addStretch(1)

        def target():   # 大 json 读盘放后台线程
            result = B.run_pae_request(dict(action='load',path=conf,input=inp), cancellation)
            return result['pae'], '', result['chains'], result['ds']

        def done(err, res):
            if token != self._pae_token:      # 已经又选了别的模型,丢弃过期结果
                return
            pae, note, chains, ds = res if res else (None, None, [], 1)
            self._clear(self.pae_holder)
            if err or pae is None:
                self.pae_holder.addWidget(UI.QLabel(UI.f('PAE load failed: {0}', err or note)))
                return
            if not HAS_MPL:
                self.pae_holder.addWidget(UI.QLabel(UI.tr('matplotlib not installed — cannot draw the PAE plot.')))
                return
            try:
                self._pae_last = (pae, chains, name, i, ds, conf)
                self._pae_file_version=B.R.file_version(conf)
                self.pae_holder.addWidget(
                    self._pae_canvas(pae, chains, ds=ds), 1)
                cap = UI.QLabel(UI.f("{0}{1}", name, (UI.f('   ·   {0}', note) if note else UI.tr(''))))
                cap.setProperty("caption", True)
                self.pae_holder.addWidget(cap)
            except Exception as e:
                self.pae_holder.addWidget(UI.QLabel(UI.f('PAE plot failed: {0}', e)))
        enqueue(self._q, target, done, pool=_PAE_WORKERS)

    def _toggle_pae_domains(self, checked):
        self._pae_dom_panel.setVisible(checked)

    def _apply_pae_domains(self):
        """按面板参数算 PAE domain 分段,在 PAE 图上叠加红色虚线边界。
        复合物:对每条蛋白链自己的对角线 block(链内 PAE 子矩阵)独立分段,
        链间 PAE 不参与;每条链一个勾选按钮,点选控制显示哪些链的边界。
        计算用完整(未降采样)矩阵,保证边界残基号精确。"""
        last = getattr(self, "_pae_last", None)
        if not last:
            UI.bind(self._pae_dom_note, 'setText', UI.tr('select a model first'))
            return
        pae, chains, name, idx, ds, conf = last
        params = {"pae_cutoff": self.pd_cut.value(),
                  "resolution": self.pd_res.value(),
                  "min_domain": self.pd_mdom.value(),
                  "domains_per_window": self.pd_dpw.value(),
                  "min_window": self.pd_mf.value(),
                  "max_span": self.pd_maxf.value() or self._pd_auto_maxf}
        entries = getattr(self, "_struct_entries", [])
        prot = (entries[idx].get("a") or entries[idx]["label"]) \
            if 0 <= idx < len(entries) else name
        # 只处理蛋白链;无链信息(老批次)时按整条单链处理
        if chains:
            blocks = [(ch["label"], ch["start"], ch["end"]) for ch in chains
                      if ch.get("type", "protein") == "protein"
                      and ch["end"] > ch["start"]]
        else:
            blocks = [(prot, 0, None)]     # 长度待完整矩阵加载后定
        if not blocks:
            UI.bind(self._pae_dom_note, 'setText', UI.tr('no protein chain in this model'))
            return
        self._pae_token += 1
        token = self._pae_token
        cancellation = self._new_pae_request('domains')
        UI.bind(self._pae_dom_note, 'setText', UI.tr('computing…'))

        analysis_chains = chains or []
        if not analysis_chains:
            UI.bind(self._pae_dom_note, 'setText', UI.tr('缺少可靠链映射，无法进行分域；请检查输入和 token 元数据'))
            return
        def target():
            return B.run_pae_request(dict(path=conf,chains=analysis_chains,params=params), cancellation)

        def done(err, res):
            if token != self._pae_token:
                return
            if err:
                UI.bind(self._pae_dom_note, 'setText', UI.f('failed: {0}', err))
                return
            self._pae_dom_results = res
            self._rebuild_chain_buttons()
            self._redraw_dom_lines()
        enqueue(self._q, target, done, pool=_PAE_WORKERS)

    def _anno_label(self, label):
        """'P12345' -> 'P12345 (entry_name)'(查不到蛋白名则原样)。"""
        acc, _ = B.split_acc_label(label)
        nm = self._names.get(acc) if acc else None
        return f"{label} ({nm})" if nm else label

    def _clear_pae_domain_controls(self):
        """Drop controls owned by the previous model/page before Qt deletes it."""
        old = getattr(self, "_pae_chain_row", None)
        self._pae_chain_row = None
        self._chain_group = None
        self._pae_detail_refresh = None
        if old is not None and isValid(old):
            parent = old.parentWidget()
            if parent is not None and parent.layout() is not None:
                parent.layout().removeWidget(old)
            old.hide()
            old.deleteLater()

    def _rebuild_chain_buttons(self):
        """链选择按钮行:All chains(整个复合物 PAE)+ 每条蛋白链一个
        (单链对角线 block),互斥单选,默认 All chains。"""
        # 清掉旧的按钮行
        self._clear_pae_domain_controls()
        row = QtWidgets.QWidget()
        h = QtWidgets.QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        h.addWidget(UI.QLabel(UI.tr('chains:')))
        self._chain_group = QtWidgets.QButtonGroup(row)
        self._chain_group.setExclusive(True)
        results = getattr(self, "_pae_dom_results", {})
        multi = len(results) > 1
        ball = UI.QPushButton(UI.tr('All chains'))
        ball.setCheckable(True)
        ball.setChecked(True)
        ball.setProperty("chain", "__ALL__")
        self._chain_group.addButton(ball)
        h.addWidget(ball)
        for lab in results:
            b = UI.QPushButton(self._anno_label(lab))
            b.setCheckable(True)
            b.setProperty("chain", lab)
            if not multi:
                b.setChecked(True)      # 单链模型:直接选中该链
            self._chain_group.addButton(b)
            h.addWidget(b)
        self._chain_group.buttonClicked.connect(
            lambda _b: self._redraw_dom_lines())
        h.addStretch(1)
        lay = self._pae_dom_panel.layout()
        lay.addWidget(row, lay.rowCount(), 0, 1, 6)
        self._pae_chain_row = row

    def _redraw_dom_lines(self):
        """按选中的链重画:All chains = 整复合物 PAE + 所有链的红虚线;
        单链 = 裁剪为该链对角线 block 的 PAE(链内坐标)+ 该链红虚线。
        图下方列出所选链的 domains/fragments 明细。"""
        last = getattr(self, "_pae_last", None)
        results = getattr(self, "_pae_dom_results", {})
        if not last or not results:
            return
        pae, chains, name, idx, ds, conf = last
        btn = self._chain_group.checkedButton()
        sel = btn.property("chain") if btn else "__ALL__"

        def detail_text(lab):
            r = results[lab]
            dmap = {d["name"]: d for d in r["domains"]}
            parts = [f"== {self._anno_label(lab)} ==",
                     ('结构域（仅 PAE 聚类参数，不含最短片段/每窗口域数）：' if self._lang=='zh' else 'Domains (PAE clustering only; excludes min-frag/domains-per-window):')]
            for d in r["domains"]:
                segs = " + ".join(f"{s}-{e}" for s, e in d["segments"])
                discont = "   [discontinuous]" if len(d["segments"]) > 1 else ""
                parts.append(
                    f"  {d['name']}: segments {segs:<28} -> cut "
                    f"{d['cut_range'][0]}-{d['cut_range'][1]}{discont}")
            parts.append('片段（红虚线划分，对应扫描窗口）：' if self._lang=='zh' else 'Fragments (red dashed boundaries; actual Scan windows):')
            for w in r["windows"]:
                members = " + ".join(
                    f"{dn}:{dmap[dn]['cut_range'][0]}-{dmap[dn]['cut_range'][1]}"
                    for dn in w["domains"] if dn in dmap)
                extra = ""
                if any(len(dmap[dn]["segments"]) > 1
                       for dn in w["domains"] if dn in dmap):
                    extra += ('  [包含非连续结构域的完整跨度]' if self._lang=='zh' else '  [includes entire span of a discontinuous domain]')
                parts.append(
                    f"  {'+'.join(w['domains'])}: {w['start']}-{w['end']} "
                    f"({w['end'] - w['start'] + 1} aa)   [= {members}]{extra}")
            if r["notes"]:
                notes=r['notes']
                if self._lang=='en':
                    import re
                    notes=[re.sub(r'按 resolution=(.+?) 再切为 (\d+) 段',r'resplit at resolution=\1 into \2 parts',n)
                           .replace('上限','limit').replace('无法再细分,整段保留(将按 token 数自动选大显存分区)',
                           'cannot split further; retain the full span (partition follows token count)') for n in notes]
                parts.append('  notes: ' + '; '.join(notes))
            return parts

        self._clear(self.pae_holder)
        if sel == "__ALL__":
            # 整复合物 PAE + 所有链边界线
            lines = []
            for lab, r in results.items():
                anno = self._anno_label(lab)
                lines += [(pos, f"{anno} · {e}") for pos, e in r["lines"]]
            self.pae_holder.addWidget(
                self._pae_canvas(pae, chains, dom_lines=lines, ds=ds), 1)
            text_parts = []
            for lab in results:
                text_parts += detail_text(lab)
        else:
            # 单链:裁剪对角线 block(降采样矩阵坐标),链内编号
            r = results[sel]
            s0, e0 = r["s0"], r["e0"]
            c0, c1 = s0 // ds, (e0 + ds - 1) // ds
            sub = [row2[c0:c1] for row2 in pae[c0:c1]]
            anno = self._anno_label(sel)
            lines = [(pos - s0, f"{anno} · {e}")
                     for pos, e in r["lines"]]
            self.pae_holder.addWidget(
                self._pae_canvas(sub, None, dom_lines=lines, ds=ds,
                                 title=f"{anno} — chain block PAE "
                                       f"(chain-local numbering)"), 1)
            text_parts = detail_text(sel)
        cap = _ConsoleText()
        cap.setReadOnly(True)
        UI.bind(cap, 'setPlainText', UI.f("{0}{1}", UI.f("{0}{1}", name, UI.tr('\n')), "\n".join(text_parts)))
        cap.setMaximumHeight(150)
        self.pae_holder.addWidget(cap)
        def refresh_details():
            if not isValid(cap):return
            parts=[]
            for lab in (list(results) if sel=='__ALL__' else [sel]):parts+=detail_text(lab)
            UI.bind(cap,'setPlainText',name+'\n'+'\n'.join(parts))
        self._pae_detail_refresh=refresh_details
        refresh_details()
        ndom = (sum(len(r["domains"]) for r in results.values())
                if sel == "__ALL__" else len(results[sel]["domains"]))
        nwin = (sum(len(r['windows']) for r in results.values()) if sel == '__ALL__' else len(results[sel]['windows']))
        UI.bind(self._pae_dom_note, 'setText', UI.f('Total: {0} PAE domains; split into {1} fragments', ndom, nwin))
        UI.bind(self._pae_dom_note, 'setToolTip', UI.tr('Red dashed boundaries mark the split fragments.'))

    def _cancel_pae_work(self):
        for event in getattr(self, '_pae_requests', {}).values(): event.set()
        B.stop_pae_processes()

    def _new_pae_request(self, kind):
        if not hasattr(self, '_pae_requests'): self._pae_requests={}
        if kind in self._pae_requests: self._pae_requests[kind].set()
        event=threading.Event();self._pae_requests[kind]=event
        return event

    def closeEvent(self,event):
        self._closing = True
        self._cancel_pae_work()
        B.stop_pae_processes(shutdown=True)
        super().closeEvent(event)

    def _pae_canvas(self, pae, chains, dom_lines=None, ds=1, title=None):
        holder=QtWidgets.QWidget();layout=QtWidgets.QVBoxLayout(holder)
        note=UI.QLabel(UI.tr('Rendering PAE…'));layout.addWidget(note)
        cancel=UI.QPushButton(UI.tr('Cancel analysis'));layout.addWidget(cancel)
        cancellation=self._new_pae_request('render')
        cancel.clicked.connect(cancellation.set)
        request=dict(action='render',chains=chains,lines=dom_lines,ds=ds,title=title)
        def done(err,result):
            if not isValid(holder):return
            cancel.hide()
            if err:
                UI.bind(note,'setText',UI.tr('Cancelled') if isinstance(err,B.AnalysisCancelled) else str(err));return
            if cancellation.is_set():return
            import base64
            pm=QtGui.QPixmap();pm.loadFromData(base64.b64decode(result['png']),'PNG')
            labels=[str(i*ds+1) for i in range(len(pae))]
            note.hide()
            layout.addWidget(HeatmapLabel(pm,pae,labels,labels,result['axes'],row_name='',col_name='',
                chains=chains or None,val_name='PAE',val_fmt='{:.1f} Å',ds=ds),1)
        enqueue(self._q,lambda:B.run_pae_request(request,cancellation,matrix=pae),done,pool=_PAE_WORKERS)
        return holder

    # ================================================================ Clean
    # (History 页已并入 Dashboard;View in Results / Rebuild resubmit / Fill into editor
    #  见 _dash_view/_dash_rebuild/_dash_prefill)
    # ---- (#1)批次清理:类别勾选 -> 再次确认 -> 后台删除 ----
    def _clean_flow(self, b):
        if not b:
            UI.QMessageBox.information(self, UI.tr('Select'), UI.tr('Please select a batch first.'))
            return
        self.statusBar().showMessage(UI.render(UI.tr('scanning batch data sizes…')), 3000)
        enqueue(self._q, lambda: B.batch_clean_targets(b),
                lambda err, res: self._clean_dialog(err, res, b))

    def _clean_dialog(self, err, targets, b):
        if err:
            UI.QMessageBox.critical(self, UI.tr('Scan failed'), str(err)); return
        if not targets and not b.get("has_ranking"):
            UI.QMessageBox.information(self, UI.tr('Nothing to clean'), UI.tr('No output dir or MSA products found for this batch.'))
            return
        dlg = UI.QDialog(self)
        UI.bind(dlg, 'setWindowTitle', UI.f('Clean batch data — {0}', b['name']))
        v = QtWidgets.QVBoxLayout(dlg)
        v.setSpacing(14)              # 段间距离:三类选项(output / msa / seed prune)平级
        v.addWidget(UI.QLabel(UI.tr('Select what to delete. This cannot be undone.\nDelete happens on the cluster, immediately.')))
        boxes = {}
        for cat, t in targets.items():
            cb = UI.QCheckBox(UI.f('{0}   ({1}, {2} files)\n{3}', cat, B.fmt_bytes(t['bytes']), t['count'], t['desc']))
            cb.setChecked(cat == "output")          # msa 默认不勾(共享池,慎删)
            boxes[cat] = cb
            v.addWidget(cb)
        if "msa" in boxes:
            warn = UI.QLabel(UI.tr('⚠ MSA is a SHARED pool: other batches using the same proteins will have to recompute those MSAs.'))
            warn.setStyleSheet("color: #b26a00")
            warn.setWordWrap(True)
            v.addWidget(warn)

        # ---- 低置信 pair 的 seed-*/ 瘦身:与前两类平级;与 output 互斥 ----
        prune_cb = None
        prune_state = {}
        if b.get("has_ranking"):
            PRUNE_HINT = ("delete the seed-*/ subdirectories of matching pairs "
                          "(the bulkiest files); top-level model.cif / confidences / "
                          "summary / data.json / ranking_scores.csv are kept")
            prune_cb = UI.QCheckBox(UI.f('seed prune   (tick to compute affected size)\n{0}', PRUNE_HINT))
            v.addWidget(prune_cb)
            prow = QtWidgets.QHBoxLayout()
            prow.setContentsMargins(28, 0, 0, 0)   # 参数行缩进,从属于 seed prune
            iptm_en = UI.QCheckBox(UI.tr('ipTM <'))
            iptm_en.setChecked(True)
            iptm_sp = UI.QDoubleSpinBox()
            iptm_sp.setRange(0.0, 1.0); iptm_sp.setSingleStep(0.05)
            iptm_sp.setValue(0.50)
            rank_en = UI.QCheckBox(UI.tr('rank >'))
            rank_sp = UI.QSpinBox()
            rank_sp.setRange(1, 999999); rank_sp.setValue(100)
            mode_cb = QtWidgets.QComboBox()
            UI.items(mode_cb, [UI.tr('both (AND)'), UI.tr('either (OR)')])
            mode_cb.setCurrentIndex(0)                    # 默认 AND,删得更保守
            for w in (iptm_en, iptm_sp, rank_en, rank_sp, mode_cb):
                prow.addWidget(w)
            prow.addStretch(1)
            v.addLayout(prow)

            ptimer = QtCore.QTimer(dlg)
            ptimer.setSingleShot(True)
            ptimer.setInterval(400)

            def prune_params():
                return (iptm_sp.value() if iptm_en.isChecked() else None,
                        rank_sp.value() if rank_en.isChecked() else None,
                        "all" if mode_cb.currentIndex() == 0 else "any")

            def prune_calc():
                if not (prune_cb.isChecked()
                        and (iptm_en.isChecked() or rank_en.isChecked())):
                    prune_state["t"] = None
                    UI.bind(prune_cb, 'setText', UI.f('seed prune   (tick to compute affected size)\n{0}', PRUNE_HINT))
                    return
                if prune_state.get("busy"):
                    ptimer.start();return
                prune_state["busy"]=True
                UI.bind(prune_cb, 'setText', UI.f('seed prune   (computing…)\n{0}', PRUNE_HINT))
                # 不走主窗口队列:模态对话框 exec() 的嵌套事件循环里队列
                # 回调投递不可靠。对话框自起线程 + 自己的轮询定时器收结果;
                # 参数先在 GUI 线程读好。
                params = prune_params()
                holder = {}

                def work():
                    try:
                        holder["res"] = B.prune_seed_targets(b, *params)
                    except Exception as e:
                        holder["err"] = e

                _WORKERS.submit(work)
                poll = QtCore.QTimer(dlg)
                poll.setInterval(150)

                def check():
                    if "res" not in holder and "err" not in holder:
                        return
                    poll.stop();prune_state["busy"]=False
                    if params != prune_params():ptimer.start();return
                    if "err" in holder:
                        UI.bind(prune_cb, 'setText', UI.f('seed prune   (scan failed: {0})\n{1}', holder['err'], PRUNE_HINT))
                        return
                    pres = holder["res"]
                    prune_state["t"] = pres
                    if not pres:
                        UI.bind(prune_cb, 'setText', UI.f('seed prune   (no matching pairs)\n{0}', PRUNE_HINT))
                    else:
                        UI.bind(prune_cb, 'setText', UI.f('seed prune   ({0}, {1} files in {2} pairs)\n{3}', B.fmt_bytes(pres['bytes']), pres['count'], pres['pairs'], pres['desc']))

                poll.timeout.connect(check)
                poll.start()

            ptimer.timeout.connect(prune_calc)
            for w in (iptm_en, rank_en):
                w.toggled.connect(lambda _=False: ptimer.start())
            iptm_sp.valueChanged.connect(lambda _=0.0: ptimer.start())
            rank_sp.valueChanged.connect(lambda _=0: ptimer.start())
            mode_cb.currentIndexChanged.connect(lambda _=0: ptimer.start())
            prune_cb.toggled.connect(lambda _=False: ptimer.start())

        # 互斥:output = 整个目录都删,seed prune(只瘦身)就无意义
        out_cb = boxes.get("output")
        if out_cb is not None and prune_cb is not None:
            out_cb.toggled.connect(
                lambda on: prune_cb.setChecked(False) if on else None)
            prune_cb.toggled.connect(
                lambda on: out_cb.setChecked(False) if on else None)

        row = QtWidgets.QHBoxLayout(); row.addStretch(1)
        no = UI.QPushButton(UI.tr('Cancel')); no.clicked.connect(dlg.reject)
        yes = UI.QPushButton(UI.tr('Delete selected')); yes.setObjectName("primary")
        yes.clicked.connect(dlg.accept)
        row.addWidget(no); row.addWidget(yes)
        v.addLayout(row)
        if not dlg.exec():
            return
        cats = [c for c, cb in boxes.items() if cb.isChecked()]
        do_prune = bool(prune_cb and prune_cb.isChecked()
                        and (iptm_en.isChecked() or rank_en.isChecked()))
        if not cats and not do_prune:
            return
        summary = "\n".join(f"  {c}: {B.fmt_bytes(targets[c]['bytes'])}"
                            for c in cats)
        if do_prune:
            t = prune_state.get("t")
            summary += ("\n  seed prune: " +
                        (f"{t['pairs']} pairs, {B.fmt_bytes(t['bytes'])}"
                         if t else "recomputed at delete time"))
        r = UI.QMessageBox.question(self, UI.tr('Confirm delete'), UI.f('Batch: {0}\nReally delete:\n{1}\n\nThis cannot be undone.', b['name'], summary))
        if r != QtWidgets.QMessageBox.Yes:
            return
        if do_prune: cats.append("seeds")
        prune = prune_params() if do_prune else None
        enqueue(self._q, lambda: B.clean_batch(b, cats, prune=prune),
                lambda err, res: self._clean_done(err, res))

    def _clean_done(self, err, res):
        done, cerr = res if res else ({}, None)
        if err:
            UI.QMessageBox.critical(self, UI.tr('Clean failed'), str(err))
        elif cerr:
            UI.QMessageBox.warning(self, UI.tr('Partial clean'), str(cerr))
        else:
            total = B.fmt_bytes(sum(done.values())) if done else "0 B"
            self.statusBar().showMessage(UI.render(UI.f('cleaned: {0} freed', total)), 5000)
        self._dash_refresh()
        self._res_refresh()

    # ================================================================ Help
    def _build_help(self):
        w = QtWidgets.QWidget(); UI.tab(self.tabs, w, UI.tr('Help'))
        lay = QtWidgets.QVBoxLayout(w); lay.setContentsMargins(20, 16, 20, 16)
        lay.addLayout(self._intro("help"))
        split = QtWidgets.QSplitter(QtCore.Qt.Horizontal); lay.addWidget(split, 1)
        nav = QtWidgets.QWidget(); nl = QtWidgets.QVBoxLayout(nav); nl.setContentsMargins(0, 0, 12, 0)
        self.help_search = UI.QLineEdit(); UI.bind(self.help_search, 'setPlaceholderText', UI.tr('Search help')); self.help_search.setClearButtonEnabled(True)
        nl.addWidget(self.help_search)
        self.help_topics = QtWidgets.QListWidget(); self.help_topics.setObjectName("helpnav"); nl.addWidget(self.help_topics, 1); nav.setMinimumWidth(170); nav.setMaximumWidth(290)
        split.addWidget(nav)
        self.help_body = UI.ReadableBrowser(); self.help_body.setOpenExternalLinks(False)
        self.help_body.anchorClicked.connect(self._help_link); split.addWidget(self.help_body)
        split.setSizes([220, 900]); self._help_topic_key = "quickstart"
        self.help_topics.currentRowChanged.connect(self._help_article)
        self.help_search.textChanged.connect(self._help_filter)
        self._help_filter()

    def _help_filter(self, *_):
        query = self.help_search.text().strip().casefold()
        self.help_topics.blockSignals(True); self.help_topics.clear()
        chosen = -1
        for key, zh, en, zb, eb in H.HELP_SECTIONS:
            title, body = (zh, zb) if self._lang == "zh" else (en, eb)
            if query and query not in (zh + " " + en + " " + zb + " " + eb).casefold(): continue
            item = QtWidgets.QListWidgetItem(title); item.setData(QtCore.Qt.UserRole, key); self.help_topics.addItem(item)
            if key == self._help_topic_key: chosen = self.help_topics.count()-1
        self.help_topics.setCurrentRow(chosen if chosen >= 0 else (0 if self.help_topics.count() else -1))
        self.help_topics.blockSignals(False); self._help_article()

    def _help_article(self, *_):
        item = self.help_topics.currentItem()
        if not item: self.help_body.clear(); return
        self._help_topic_key = item.data(QtCore.Qt.UserRole)
        self.help_body.setHtml(H.article_html(self._help_topic_key, self._lang, CC))
        selections=[];query=self.help_search.text().strip()
        if query:
            cursor=self.help_body.document().find(query)
            while not cursor.isNull() and len(selections)<200:
                selection=QtWidgets.QTextEdit.ExtraSelection();selection.cursor=cursor
                selection.format.setBackground(QtGui.QColor('#e7b88a'));selection.format.setForeground(QtGui.QColor('#252525'))
                selections.append(selection);cursor=self.help_body.document().find(query,cursor)
        self.help_body.setExtraSelections(selections)

    def _help_link(self, url):
        # Known local actions only; license links open an offline text viewer.
        if url.scheme() == "example":
            example = H.EXAMPLES.get(url.path(), "")
            if example: self._copy(example)
        elif url.scheme() == "param": self._show_help(url.path())
        elif url.scheme() == "help":
            self._help_topic_key = url.path(); self.help_search.clear(); self._help_filter()
        elif url.scheme() == "license":
            key = url.path()
            try:
                title, content = H.license_text(key)
            except (OSError, ValueError) as exc:
                UI.QMessageBox.warning(self, UI.tr('License text'), str(exc))
                return
            dialog = QtWidgets.QDialog(self)
            dialog.setWindowTitle(title)
            dialog.resize(800, 600)
            layout = QtWidgets.QVBoxLayout(dialog)
            viewer = QtWidgets.QPlainTextEdit()
            viewer.setReadOnly(True)
            viewer.setPlainText(content)
            layout.addWidget(viewer)
            close = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Close)
            close.rejected.connect(dialog.reject)
            layout.addWidget(close)
            dialog.exec()

    # ================================================================ misc
    # (#1)Download 按钮已全部移除:界面跑在集群,保存只能选集群路径,没意义;
    # 统一改为 Copy folder path(MobaXterm SFTP 面板只接受文件夹)+ Copy content。


# ----------------------------------------------------------------------------
# 双主题:light = ccDesign(暖奶油 + 珊瑚,见 ccDesign.md);
#         dark  = Sanity 风(近黑 canvas + 电光蓝激活,见 SanityDarkDesign.md)。
# 全部组件颜色走 CC token;_CC_QSS 里的 $var 由 string.Template 替换,切主题 = 换调色板重套。
# ----------------------------------------------------------------------------
_THEMES = {
    # ccDesign:canvas #faf9f5 → 卡片 #efe9de → 输出卡 #f5f0e8;珊瑚 CTA
    "light": {
        "primary": "#cc785c", "primary_active": "#a9583e", "primary_disabled": "#e6dfd8",
        "ink": "#141413", "body": "#3d3d3a", "muted": "#6c6a64", "muted_soft": "#8e8b82",
        "hairline": "#e6dfd8", "hairline_soft": "#ebe6df",
        "canvas": "#faf9f5", "surface_soft": "#f5f0e8", "surface_card": "#efe9de",
        "cream_strong": "#e8e0d2",
        "dark": "#181715", "dark_elev": "#252320", "dark_soft": "#1f1e1b",
        "on_dark": "#faf9f5", "on_dark_soft": "#a09d96",
        "teal": "#5db8a6", "amber": "#e8a55a", "success": "#5db872",
        "warning": "#d4a017", "error": "#c64545",
    },
    # Sanity:canvas #0b0b0b → 卡片 #212121 → 描边 #353535;珊瑚红 CTA #f36458;
    # 激活/按下统一电光蓝 #0052ef;文字白 / 银 / 中灰
    "dark": {
        "primary": "#f36458", "primary_active": "#0052ef", "primary_disabled": "#353535",
        "ink": "#ffffff", "body": "#b9b9b9", "muted": "#797979", "muted_soft": "#797979",
        "hairline": "#353535", "hairline_soft": "#212121",
        "canvas": "#0b0b0b", "surface_soft": "#161616", "surface_card": "#212121",
        "cream_strong": "#353535",
        "dark": "#000000", "dark_elev": "#212121", "dark_soft": "#161616",
        "on_dark": "#ffffff", "on_dark_soft": "#b9b9b9",
        "teal": "#55beff", "amber": "#e8b574", "success": "#73cbaa",
        "warning": "#e8b574", "error": "#ff8d82",
    },
    # AppleDesign.md: neutral white/gray surfaces, action blue and fine borders.
    "apple": {
        "primary": "#0066cc", "primary_active": "#0071e3", "primary_disabled": "#e5e5ea",
        "ink": "#1d1d1f", "body": "#333333", "muted": "#626266", "muted_soft": "#7a7a7a",
        "hairline": "#e0e0e0", "hairline_soft": "#f0f0f0",
        "canvas": "#ffffff", "surface_soft": "#f5f5f7", "surface_card": "#fafafc",
        "cream_strong": "#e8f2ff",
        "dark": "#1d1d1f", "dark_elev": "#272729", "dark_soft": "#333333",
        "on_dark": "#ffffff", "on_dark_soft": "#cccccc",
        "teal": "#0066cc", "amber": "#956000", "success": "#28823e",
        "warning": "#956000", "error": "#b3261e",
    },
}
_THEME_NAMES = {'apple':'Minimal', 'light':'Light', 'dark':'Dark'}
CC = dict(_THEMES["light"])   # 当前调色板;切主题时原地 clear+update,引用不失效
def _pick_serif(app):
    """Choose a serif title font without enumerating remote font directories.

    Accept only a known serif match; otherwise retain the application font to
    avoid accidentally using an icon font as a system fallback.
    """
    cands = ["Georgia", "DejaVu Serif", "Times New Roman",
             "Liberation Serif", "Noto Serif", "Nimbus Roman", "FreeSerif"]
    try:
        f = QtGui.QFont()
        f.setFamilies(cands)
        hit = QtGui.QFontInfo(f).family()
        if hit in cands:
            return '"%s"' % hit
    except Exception:
        pass
    return '"%s"' % app.font().family()


def _apply_theme(app, theme="light", font_qss=None):
    """套用主题(token 见 _THEMES;_CC_QSS 里的 $var 由 string.Template 替换)。
    font_qss:字号规则,传了就合并进同一次 setStyleSheet——切主题时只做
    一次全窗口 restyle(过 X11 每次 restyle 都是一轮重绘,能省一半时间)。
    除 QSS 外同步刷 QPalette:placeholder / tooltip 等原生绘制的颜色不走 QSS。"""
    from string import Template
    global _SERIF_FAM
    pal = _THEMES.get(theme, _THEMES["light"])
    CC.clear(); CC.update(pal)               # 原地更新,运行时引用(CC[...])即刻生效
    _SERIF_FAM = _pick_serif(app).strip('"')
    ss = Template(_CC_QSS).substitute(CC, serif='"%s"' % _SERIF_FAM)
    if theme == 'apple': ss += '\n' + _APPLE_QSS
    if font_qss:
        ss += f"\n{_FONT_RULE_MARK}\n{font_qss}\n"
    app.setStyleSheet(ss)
    qp = app.palette()
    for role, key in ((QtGui.QPalette.Window, "canvas"),
                      (QtGui.QPalette.WindowText, "ink"),
                      (QtGui.QPalette.Base, "canvas"),
                      (QtGui.QPalette.AlternateBase, "surface_soft"),
                      (QtGui.QPalette.Text, "ink"),
                      (QtGui.QPalette.Button, "canvas"),
                      (QtGui.QPalette.ButtonText, "ink"),
                      (QtGui.QPalette.PlaceholderText, "muted"),
                      (QtGui.QPalette.ToolTipBase, "dark"),
                      (QtGui.QPalette.ToolTipText, "on_dark"),
                      (QtGui.QPalette.Highlight, "primary")):
        qp.setColor(role, QtGui.QColor(pal[key]))
    qp.setColor(QtGui.QPalette.HighlightedText, QtGui.QColor("#ffffff"))
    app.setPalette(qp)


_SERIF_FAM = None   # _apply_theme 里确定(给 report.md 等富文本的 CSS 用)
_APPLE_QSS = """
/* Skin only: no padding, sizing, positioning, transparency or blur changes. */
QWidget#brandbar { background-color: #f5f5f7; }
QPushButton { background-color: #fafafc; color: #0066cc; }
QPushButton#primary { background-color: #0066cc; color: #ffffff; border-radius: 18px; }
QPushButton#primary:pressed { background-color: #0071e3; }
QPushButton#primary:disabled { background-color: #e5e5ea; color: #7a7a7a; }
QPushButton#darkAction { background-color: #1d1d1f; color: #ffffff; }
QPushButton#darkAction:hover { background-color: #333333; }
QPushButton#darkAction:disabled { background-color: #f5f5f7; color: #7a7a7a; }
QPushButton:focus, QToolButton:focus { border-color: #0071e3; }
QTabBar::tab:selected { background-color: #e8f2ff; color: #0066cc; }
"""

_CC_QSS = """
QWidget { background-color: $canvas; color: $ink; }
QLabel, QCheckBox, QRadioButton { background: transparent; }
QLabel#pagehead { font-weight: 600; font-size: 19pt; color: $ink; }
QLabel#sectionhead { font-weight: 600; color: $muted; padding-top: 8px; }
QLabel[caption="true"] { color: $muted; }

/* top-nav:奶油品牌栏,底部 1px hairline */
QWidget#brandbar { background-color: $canvas; border-bottom: 1px solid $hairline; }
QLabel#wordmark { font-family: $serif; font-weight: 400; font-size: 17pt; color: $ink; background: transparent; }
QLabel#brandsub { color: $muted_soft; background: transparent; }

/* feature-card:分组框 = 奶油卡片(比 canvas 深一档),12px 圆角;
   margin-top 留足标题高度,标题才不与框体/内容交错 */
QGroupBox {
  background-color: $canvas; border: 1px solid $hairline; border-radius: 8px;
  margin-top: 22px; padding: 10px 12px 10px 12px; font-weight: 500;
}
QGroupBox::title {
  subcontrol-origin: margin; subcontrol-position: top left;
  left: 12px; top: 6px; color: $muted;
}

/* button-secondary:canvas 底 + hairline 描边;按下变奶油卡色 */
QPushButton {
  background-color: $canvas; color: $ink;
  border: 1px solid $hairline; border-radius: 8px;
  padding: 6px 16px; min-height: 24px;
}
QPushButton:pressed { background-color: $surface_card; }
QPushButton:disabled {
  background-color: $primary_disabled; color: $muted; border-color: $primary_disabled;
}
/* button-primary:签名珊瑚 CTA,只在按下时加深(无 hover 特效) */
QPushButton#primary {
  background-color: $primary; color: #ffffff; border: none; font-weight: 500;
}
QPushButton#primary:pressed { background-color: $primary_active; }
QPushButton#primary:disabled { background-color: $primary_disabled; color: $muted; }

QPushButton#darkAction {
  background-color: $dark_elev; color: $on_dark; border: 1px solid $dark_elev;
  font-weight: 500; padding: 6px 16px;
}
QPushButton#darkAction:hover { background-color: $dark_soft; border-color: $muted; }
QPushButton#darkAction:pressed { background-color: $dark; }
QPushButton#darkAction:disabled { background-color: $surface_card; color: $muted; }

QToolButton {
  background: transparent; color: $ink;
  border: 1px solid $hairline; border-radius: 8px; padding: 3px 9px;
}
QToolButton:pressed { background-color: $surface_card; }
QToolButton[help="true"] { border: none; color: $muted_soft; padding: 2px; }

/* text-input:canvas 底 + hairline;聚焦时边框变珊瑚 */
QLineEdit, QSpinBox, QComboBox, QPlainTextEdit, QTextEdit {
  background-color: $canvas; color: $ink;
  border: 1px solid $hairline; border-radius: 8px; padding: 5px 9px;
  selection-background-color: $primary; selection-color: #ffffff;
}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus, QPlainTextEdit:focus, QTextEdit:focus {
  border: 1px solid $primary;
}
QLineEdit:read-only { background-color: $surface_soft; }
QComboBox::drop-down { border: none; width: 22px; }
QComboBox::down-arrow {
  border-left: 4px solid transparent; border-right: 4px solid transparent;
  border-top: 5px solid $muted; width: 0; height: 0; margin-right: 8px;
}
QComboBox QAbstractItemView {
  background-color: $canvas; color: $ink; border: 1px solid $hairline;
  selection-background-color: $surface_card; selection-color: $ink; outline: none;
}
QSpinBox::up-button, QSpinBox::down-button { border: none; width: 14px; background: transparent; }
QSpinBox::up-arrow {
  border-left: 4px solid transparent; border-right: 4px solid transparent;
  border-bottom: 5px solid $muted; width: 0; height: 0;
}
QSpinBox::down-arrow {
  border-left: 4px solid transparent; border-right: 4px solid transparent;
  border-top: 5px solid $muted; width: 0; height: 0;
}

/* 输出卡:只读输出 = 浅奶油卡(不用深色,与输入区区分靠底色+圆角) */
QPlainTextEdit#console, QTextBrowser#console {
  background-color: $surface_soft; color: $body;
  border: 1px solid $hairline; border-radius: 12px; padding: 8px 10px;
  selection-background-color: $primary; selection-color: #ffffff;
}

/* category-tab:非选中透明灰字,选中奶油卡底黑字 */
QTabWidget::pane { border: none; }
QTabBar::tab {
  background: transparent; color: $muted; font-weight: 500;
  padding: 7px 14px; margin: 2px; border-radius: 8px;
}
QTabBar::tab:selected { background-color: $surface_card; color: $ink; }
/* 窗口变窄时的左右滚动钮:深底深箭头,一眼可见 */
QTabBar QToolButton {
  background-color: $cream_strong; color: $ink;
  border: 1px solid $hairline; border-radius: 6px; margin: 2px;
}
QTabBar QToolButton:pressed { background-color: $surface_card; }
QTabBar QToolButton::left-arrow {
  image: none; width: 0; height: 0;
  border-top: 5px solid transparent; border-bottom: 5px solid transparent;
  border-right: 7px solid $ink;
}
QTabBar QToolButton::right-arrow {
  image: none; width: 0; height: 0;
  border-top: 5px solid transparent; border-bottom: 5px solid transparent;
  border-left: 7px solid $ink;
}

/* 线性进度条(PAE loading 等):奶油槽 + 珊瑚条 */
QProgressBar {
  background-color: $surface_card; border: none; border-radius: 5px;
  min-height: 10px; max-height: 10px;
}
QProgressBar::chunk { background-color: $primary; border-radius: 5px; }

QTableView, QTableWidget, QTreeWidget, QListView {
  background-color: $canvas; color: $ink;
  alternate-background-color: $surface_soft;
  border: 1px solid $hairline; border-radius: 8px; gridline-color: $hairline_soft;
  selection-background-color: $cream_strong; selection-color: $ink;
}
QTableWidget::item { border: none; }
QListWidget#helpnav::item { padding: 9px 10px; }
QHeaderView::section {
  background-color: $surface_soft; color: $muted; font-weight: 500;
  border: none; border-bottom: 1px solid $hairline; padding: 5px 8px;
}

/* Dashboard 指标卡 = feature-card */
QLabel[card="true"] {
  background-color: $surface_card; border-radius: 12px;
  padding: 8px 12px; color: $ink; font-weight: 500;
}

QCheckBox::indicator, QRadioButton::indicator {
  width: 15px; height: 15px; border: 1px solid $hairline; background-color: $canvas;
}
QCheckBox::indicator { border-radius: 4px; }
QRadioButton::indicator { border-radius: 8px; }
QCheckBox::indicator:checked, QRadioButton::indicator:checked {
  background-color: $primary; border-color: $primary;
}

QMenu { background-color: $canvas; border: 1px solid $hairline; padding: 4px; }
QMenu::item { padding: 5px 18px; border-radius: 6px; }
QMenu::item:selected { background-color: $surface_card; }
QToolTip { background-color: $dark; color: $on_dark; border: none; padding: 5px 8px; }

QStatusBar { background-color: $surface_soft; color: $muted; }
QStatusBar::item { border: none; }
QSplitter::handle { background-color: $hairline_soft; }
QSplitter::handle:horizontal { width: 2px; }
QSplitter::handle:vertical { height: 2px; }
QSplitter#paneSplitter::handle:horizontal { width: 8px; }
QSplitter#paneSplitter::handle:vertical { height: 8px; }
QSplitter#paneSplitter::handle:hover { background-color: $primary; }

QScrollBar:vertical { background: transparent; width: 10px; margin: 1px; }
QScrollBar::handle:vertical { background-color: $hairline; border-radius: 5px; min-height: 28px; }
QScrollBar::handle:vertical:hover { background-color: $muted_soft; }
QScrollBar:horizontal { background: transparent; height: 10px; margin: 1px; }
QScrollBar::handle:horizontal { background-color: $hairline; border-radius: 5px; min-width: 28px; }
QScrollBar::handle:horizontal:hover { background-color: $muted_soft; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }

QDialog, QMessageBox { background-color: $canvas; }
QTextBrowser {
  background-color: $canvas; border: 1px solid $hairline; border-radius: 8px;
}
"""

_FONT_RULE_MARK = "/* AF3_FONT_RULE */"


def _register_cjk_font(app):
    """注册随仓库 fonts/ 打包的开源中文字体(文泉驿微米黑),并设为默认字体。
    字体随仓库走,任何节点(没装 CJK 字体、无管理员)都能显示中文——点开即用。"""
    try:
        fdir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")
        for fn in sorted(os.listdir(fdir)) if os.path.isdir(fdir) else []:
            if not fn.lower().endswith((".ttc", ".ttf", ".otf")):
                continue
            fid = QtGui.QFontDatabase.addApplicationFont(os.path.join(fdir, fn))
            if fid >= 0:
                fams = QtGui.QFontDatabase.applicationFontFamilies(fid)
                if fams:
                    base = app.font()
                    base.setFamily(fams[0])
                    app.setFont(base)
                    return fams[0]
    except Exception:
        pass
    return None


def _install_qt_msg_filter():
    """Qt 样式引擎在 0 尺寸/尚未就绪的部件上试绘时会刷一串无害 warning
    (QPainter::begin: Paint device returned engine == 0 …、QFont::setPixelSize <= 0)。
    功能无影响但会把终端刷花——只挡这几种已知噪音,其余 Qt 消息照常打印。"""

    def _is_noise(msg):
        return (msg.startswith("QPainter::begin: Paint device returned engine == 0")
                or (msg.startswith("QPainter::") and "Painter not active" in msg)
                or msg.startswith("QPainter::restore: Unbalanced save/restore")
                or msg.startswith("QFont::setPixelSize: Pixel size <= 0"))

    def handler(mode, ctx, msg):
        if not _is_noise(msg):
            sys.stderr.write(msg + "\n")
    QtCore.qInstallMessageHandler(handler)


def main():
    if sys.platform.startswith("linux") and not os.environ.get("DISPLAY"):
        sys.stderr.write(
            "未检测到 X11($DISPLAY 为空),桌面 GUI 无法显示窗口。\n\n"
            "请用以下任一方式登录后再运行 af3_gui:\n"
            "  · MobaXterm 直接登录(自带 X server,默认开 X11);\n"
            "  · 终端:ssh -X 你的用户名@登录节点\n\n"
            "请启用 X11 转发后重试。\n")
        sys.exit(2)

    global _MONO
    _install_qt_msg_filter()               # 挡住 Qt 绘制引擎的无害告警刷屏
    app = QtWidgets.QApplication(sys.argv)
    app.setStyle("Fusion")
    fam = _register_cjk_font(app)          # 先注册打包的中文字体
    base = app.font(); base.setPointSize(11); app.setFont(base)   # (#9)默认大一号
    _theme = str(QtCore.QSettings("af3_console", "public").value("theme", "light"))
    _apply_theme(app, _theme)              # ccDesign 暖色 / Sanity 深色(记住上次选择)
    if fam:                                # 主题可能带字体声明,重新 assert CJK 字体
        f = app.font(); f.setFamily(fam); app.setFont(f)
    try:
        _MONO = QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.FixedFont)
        _MONO.setPointSize(10)
    except Exception:
        _MONO = None

    win = App()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
