"""Small native Qt building blocks and declarative, state-preserving text bindings.

Imported after the GUI's six/dateutil preload. No plotting or browser dependency.
"""
import weakref
try:
    import six.moves
    import dateutil.tz, dateutil.parser, dateutil.relativedelta, dateutil.rrule, dateutil.easter
except ImportError:
    pass
from PySide6 import QtCore, QtGui, QtWidgets
from shiboken6 import isValid
import af3_ui_text as L
import af3_ui_brand as BRAND


class BrandCat(QtWidgets.QLabel):
    """Display the small embedded cat with smooth, DPI-aware scaling."""
    def __init__(self,parent=None):
        import base64
        super().__init__(parent)
        self._original=QtGui.QPixmap()
        self._original.loadFromData(base64.b64decode(BRAND.CAT_PNG),'PNG')
        self.setFixedSize(42,42)
        self.setAlignment(QtCore.Qt.AlignCenter)
        self.setAccessibleName('AF3 Console')
        self._render()

    def _render(self):
        ratio=self.devicePixelRatioF()
        size=QtCore.QSize(round(self.width()*ratio),round(self.height()*ratio))
        pixmap=self._original.scaled(size,QtCore.Qt.KeepAspectRatio,QtCore.Qt.SmoothTransformation)
        pixmap.setDevicePixelRatio(ratio)
        self.setPixmap(pixmap)

    def resizeEvent(self,event):
        super().resizeEvent(event)
        self._render()


class Message(str):
    def __new__(cls, key, values=(), style="format"):
        obj = super().__new__(cls, key)
        obj.key, obj.values, obj.style = key, values, style
        return obj


LANG = "zh"
_BOUND = weakref.WeakValueDictionary()


def tr(key):
    return Message(key)


def f(key, *values):
    return Message(key, values)


def percent(key, values):
    return Message(key, values if isinstance(values, tuple) else (values,), "percent")


def render(value):
    if isinstance(value, Message):
        pair = L.TEXT.get(value.key)
        template = pair[0 if LANG == "zh" else 1] if pair else value.key
        args = tuple(render(x) if isinstance(x, Message) else x for x in value.values)
        return template % args if value.style == "percent" else (template.format(*args) if args else template)
    return str(value)


def lookup(value):
    return Message(value) if type(value) is str and value in L.TEXT else value


def bind(widget, method, value, *prefix):
    value = lookup(value)
    bindings = getattr(widget, "_ui_bindings", None)
    if bindings is None:
        bindings = widget._ui_bindings = {}
    slot = (method, prefix)
    if isinstance(value, Message):
        bindings[slot] = value
        _BOUND[id(widget)] = widget
    else:
        bindings.pop(slot, None)
    # Call the Qt base, bypassing our text-binding override.
    target = getattr(super(_TextMixin, widget), method) if isinstance(widget, _TextMixin) else getattr(widget, method)
    target(*prefix, render(value))
    if method == "setText" and isinstance(value, Message) and value.key.startswith("--") and isinstance(widget, QtWidgets.QWidget):
        QtWidgets.QWidget.setToolTip(widget, value.key)
    if method == "setText" and isinstance(widget, QtWidgets.QLineEdit) and not widget.hasFocus():
        widget.setCursorPosition(0)


def set_language(lang):
    global LANG
    LANG = "zh" if lang == "zh" else "en"
    for widget in list(_BOUND.values()):
        if not isValid(widget):
            continue
        for (method, prefix), message in list(widget._ui_bindings.items()):
            bind(widget, method, message, *prefix)


class _TextMixin:
    def __init__(self, *args, **kwargs):
        text = args[0] if args and isinstance(args[0], str) else None
        super().__init__(*( (render(lookup(text)),) + args[1:] if text is not None else args), **kwargs)
        if text is not None:
            method = "setTitle" if isinstance(self, QtWidgets.QGroupBox) else "setText"
            bind(self, method, text)

    def setText(self, value): bind(self, "setText", value)
    def setTitle(self, value): bind(self, "setTitle", value)
    def setToolTip(self, value): bind(self, "setToolTip", value)
    def setWindowTitle(self, value): bind(self, "setWindowTitle", value)
    def setPlaceholderText(self, value): bind(self, "setPlaceholderText", value)
    def setSpecialValueText(self, value): bind(self, "setSpecialValueText", value)
    def setPlainText(self, value): bind(self, "setPlainText", value)


class QMessageBox(QtWidgets.QMessageBox):
    @staticmethod
    def _show(method, *args, **kwargs):
        return getattr(QtWidgets.QMessageBox, method)(*(render(lookup(a)) if isinstance(a, str) else a for a in args), **kwargs)
    @staticmethod
    def information(*a, **k): return QMessageBox._show("information", *a, **k)
    @staticmethod
    def warning(*a, **k): return QMessageBox._show("warning", *a, **k)
    @staticmethod
    def critical(*a, **k): return QMessageBox._show("critical", *a, **k)
    @staticmethod
    def question(*a, **k): return QMessageBox._show("question", *a, **k)


# Only our explicitly imported controls participate. Qt itself is never patched.
for _name in ("QLabel", "QPushButton", "QToolButton", "QCheckBox", "QRadioButton",
              "QGroupBox", "QLineEdit", "QPlainTextEdit", "QTextBrowser", "QSpinBox",
              "QDoubleSpinBox", "QDialog"):
    globals()[_name] = type(_name, (_TextMixin, getattr(QtWidgets, _name)), {})


def tab(tabs, child, title):
    index = tabs.addTab(child, render(lookup(title)))
    bind(tabs, "setTabText", title, index)
    return index


def items(combo, titles):
    combo.addItems([render(lookup(t)) for t in titles])
    for index, title in enumerate(titles): bind(combo, "setItemText", title, index)


def action(menu, title):
    result = menu.addAction(render(lookup(title)))
    bind(result, "setText", title)
    return result


class ReadableBrowser(QTextBrowser):
    """Native rich text with real paragraph spacing and bounded reading width."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMaximumWidth(1080)
        self.document().setDocumentMargin(24)
        # anchorClicked is routed by the app. Qt must not navigate custom URLs
        # afterwards, or param:/example: replaces the current document with nothing.
        self.setOpenLinks(False)
        self.setOpenExternalLinks(False)
        self.setWordWrapMode(QtGui.QTextOption.WrapAtWordBoundaryOrAnywhere)

    def setHtml(self, value):
        super().setHtml(value)
        # Qt rich text supports a subset of CSS; explicitly set paragraph height.
        block = self.document().begin()
        while block.isValid():
            cursor = QtGui.QTextCursor(block)
            fmt = block.blockFormat(); fmt.setLineHeight(150.0, QtGui.QTextBlockFormat.ProportionalHeight.value)
            cursor.setBlockFormat(fmt)
            block = block.next()


def status(code):
    return render(tr("state." + (code or "unknown"))) if "state." + (code or "unknown") in L.TEXT else code


def kind(code):
    return render(tr("type." + code)) if "type." + code in L.TEXT else code


class ResponsiveColumns(QtWidgets.QWidget):
    """Resizable panes; retain independent wide/narrow sizes and editor state."""
    def __init__(self, widgets=(), threshold=900, parent=None):
        super().__init__(parent)
        self.threshold = threshold
        self.box = QtWidgets.QBoxLayout(QtWidgets.QBoxLayout.LeftToRight, self)
        self.box.setContentsMargins(0, 0, 0, 0); self.box.setSpacing(0)
        self.splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.setHandleWidth(8)
        self.splitter.setObjectName('paneSplitter')
        self.box.addWidget(self.splitter)
        self._sizes = {}; self._weights = []
        self.splitter.splitterMoved.connect(self._remember_sizes)
        for w in widgets: self.addPanel(w)

    def addPanel(self, widget, stretch=1):
        self._weights.append(stretch)
        self.splitter.addWidget(widget)
        self.splitter.setStretchFactor(self.splitter.count()-1, stretch)
        self.splitter.setSizes([weight*200 for weight in self._weights])

    def _remember_sizes(self, *_):
        sizes = self.splitter.sizes()
        if sizes and all(sizes): self._sizes[self.splitter.orientation()] = sizes

    def resizeEvent(self, event):
        narrow = self.width() < self.threshold
        orientation = QtCore.Qt.Vertical if narrow else QtCore.Qt.Horizontal
        self.box.setDirection(QtWidgets.QBoxLayout.TopToBottom if narrow else QtWidgets.QBoxLayout.LeftToRight)
        if self.splitter.orientation() != orientation:
            self._remember_sizes()
            self.splitter.setOrientation(orientation)
            self.splitter.setSizes(self._sizes.get(orientation, [weight*200 for weight in self._weights]))
        super().resizeEvent(event)


class OutputPanel(QtWidgets.QWidget):
    def __init__(self, editor, parent=None, title="Output and command", always_open=False):
        super().__init__(parent)
        box = QtWidgets.QVBoxLayout(self); box.setContentsMargins(0, 0, 0, 0)
        self.always_open = always_open
        self.editor = editor
        if always_open:
            self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
            editor.setParent(self); editor.hide(); editor._feedback_panel = self
            editor.document().setMaximumBlockCount(2000)
            self.toggle = QLabel(tr('Run feedback'))
            self.expand = QPushButton(tr('Open feedback details'))
            self.expand.setStyleSheet('padding: 2px 9px;')
            self.expand.clicked.connect(self.open_details)
            row = QtWidgets.QHBoxLayout(); row.addWidget(self.toggle); row.addStretch(); row.addWidget(self.expand)
            box.addLayout(row); box.setSpacing(3)
            self.preview = QPlainTextEdit(); self.preview.setReadOnly(True)
            self.preview.setObjectName('console'); self.preview.setLineWrapMode(QtWidgets.QPlainTextEdit.NoWrap)
            self.preview.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
            self.preview.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
            self.preview.setFont(editor.font()); box.addWidget(self.preview)
            self.preview.installEventFilter(self); editor.installEventFilter(self)
            self._fit_preview()
            self.set_summary(tr('No action yet.'), tr('Fill in the form, then preview the plan.'))
            return
        self.toggle = QLabel(tr("Run feedback")) if always_open else QPushButton(tr(title))
        if not always_open: self.toggle.setCheckable(True)
        self.toggle.setStyleSheet("text-align: left")
        box.addWidget(self.toggle); box.addWidget(editor)
        editor.setMinimumHeight(140)
        editor.setMaximumHeight(300)
        editor.setVisible(always_open)
        self.editor = editor
        if not always_open:
            self.toggle.toggled.connect(editor.setVisible)
            editor.textChanged.connect(self._show_content)

    def _show_content(self):
        if self.editor.toPlainText().strip(): self.toggle.setChecked(True)

    def _fit_preview(self):
        height = 4*self.preview.fontMetrics().lineSpacing() + 2*self.preview.document().documentMargin() + 2*self.preview.frameWidth()
        self.preview.setFixedHeight(round(height))

    def eventFilter(self, obj, event):
        if self.always_open and event.type() in (QtCore.QEvent.FontChange, QtCore.QEvent.StyleChange):
            if obj is self.editor: self.preview.setFont(self.editor.font())
            QtCore.QTimer.singleShot(0, self._fit_preview)
        return super().eventFilter(obj, event)

    def set_summary(self, *lines):
        lines = [line if isinstance(line,Message) else ' '.join(str(line).split()) for line in lines[:4]] + ['']*max(0,4-len(lines))
        message = f('{0}\n{1}\n{2}\n{3}', *lines)
        bind(self.preview, 'setPlainText', message)
        bind(self.preview, 'setToolTip', message)

    def open_details(self):
        dialog = getattr(self, '_details', None)
        if dialog is None:
            dialog = self._details = QDialog(self.window())
            bind(dialog, 'setWindowTitle', tr('Feedback details'))
            dialog.resize(960, 600)
            layout = QtWidgets.QVBoxLayout(dialog)
            view = self.details_view = QPlainTextEdit(); view.setReadOnly(True)
            view.setFont(self.editor.font()); view.setDocument(self.editor.document())
            layout.addWidget(view, 1)
            note = QLabel(tr('Recent command output; full task logs are available in Dashboard.'))
            note.setWordWrap(True); layout.addWidget(note)
            row = QtWidgets.QHBoxLayout(); copy = QPushButton(tr('Copy all'))
            copy.clicked.connect(lambda: QtGui.QGuiApplication.clipboard().setText(self.editor.toPlainText()))
            row.addWidget(copy); row.addStretch(); close = QPushButton(tr('Close'))
            close.clicked.connect(dialog.close); row.addWidget(close); layout.addLayout(row)
        self.details_view.setFont(self.editor.font())
        dialog.show(); dialog.raise_(); dialog.activateWindow()


class BatchModel(QtCore.QAbstractTableModel):
    HEADERS = ("Task", "Type", "Status", "Updated")
    def __init__(self, batches, parent=None):
        super().__init__(parent)
        self.rows = sorted(batches,key=lambda b:b.get("mtime",0),reverse=True)
        self.search = [" ".join(str(b.get(k, "")) for k in
                       ("display_name", "name", "internal_name", "short_id", "id", "type", "status")).casefold() for b in self.rows]

    def rowCount(self, parent=QtCore.QModelIndex()): return 0 if parent.isValid() else len(self.rows)
    def columnCount(self, parent=QtCore.QModelIndex()): return 4
    def headerData(self, section, orientation, role=QtCore.Qt.DisplayRole):
        if orientation == QtCore.Qt.Horizontal and role == QtCore.Qt.DisplayRole:
            return render(tr(self.HEADERS[section]))
    def data(self, index, role=QtCore.Qt.DisplayRole):
        if not index.isValid(): return None
        b = self.rows[index.row()]
        if role == QtCore.Qt.UserRole: return b["id"]
        if role == QtCore.Qt.ToolTipRole: return b["dir"]
        if role == QtCore.Qt.DisplayRole:
            if index.column() == 0:
                return b.get("display_name", b["name"])
            if index.column() == 1: return kind(b.get("type", ""))
            if index.column() == 2: return status(b.get("status", ""))
            return QtCore.QDateTime.fromSecsSinceEpoch(int(b.get("mtime", 0))).toString("yyyy-MM-dd HH:mm")


class BatchNameDelegate(QtWidgets.QStyledItemDelegate):
    def paint(self, painter, option, index):
        opts = QtWidgets.QStyleOptionViewItem(option); self.initStyleOption(opts,index)
        title = opts.text; opts.text = ""
        style = opts.widget.style() if opts.widget else QtWidgets.QApplication.style()
        style.drawControl(QtWidgets.QStyle.CE_ItemViewItem,opts,painter,opts.widget)
        proxy=index.model();b=proxy.sourceModel().rows[proxy.mapToSource(index).row()]
        source=render(tr("MSA / inference pool" if b.get("source")=="infer_pool" else "Output"))
        sub=source+" · "+b.get("short_id", "")
        rect=option.rect.adjusted(8,3,-8,-3);painter.save()
        selected=bool(option.state & QtWidgets.QStyle.State_Selected)
        painter.setPen(option.palette.color(QtGui.QPalette.HighlightedText if selected else QtGui.QPalette.Text))
        painter.drawText(rect,QtCore.Qt.AlignLeft|QtCore.Qt.AlignTop,option.fontMetrics.elidedText(title,QtCore.Qt.ElideRight,rect.width()))
        font=QtGui.QFont(option.font);font.setPointSizeF(max(8,font.pointSizeF()-1));painter.setFont(font)
        if not selected:painter.setPen(option.palette.color(QtGui.QPalette.PlaceholderText))
        painter.drawText(rect,QtCore.Qt.AlignLeft|QtCore.Qt.AlignBottom,QtGui.QFontMetrics(font).elidedText(sub,QtCore.Qt.ElideRight,rect.width()))
        painter.restore()


class BatchFilter(QtCore.QSortFilterProxyModel):
    def __init__(self, parent=None):
        super().__init__(parent); self.query = ""; self.kind = ""; self.state = ""
    def filterAcceptsRow(self, row, parent):
        model = self.sourceModel(); b = model.rows[row]
        return ((not self.kind or self.kind == b.get("type")) and
                (not self.state or self.state == b.get("status")) and
                all(word in model.search[row] for word in self.query.split()))


class BatchPicker(QDialog):
    def __init__(self, batches, selected=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("Switch task")); self.selected_id = None
        self.setModal(True)
        box = QtWidgets.QVBoxLayout(self); box.setContentsMargins(20, 20, 20, 20); box.setSpacing(12)
        self.search = QLineEdit(); self.search.setPlaceholderText(tr("Search task / UniProt ID / identifier")); self.search.setClearButtonEnabled(True)
        box.addWidget(self.search)
        filters = QtWidgets.QHBoxLayout()
        self.types = QtWidgets.QComboBox(); self.states = QtWidgets.QComboBox()
        self.types.addItem(render(tr("All types")), ""); self.states.addItem(render(tr("All states")), "")
        for k in sorted({b.get("type", "") for b in batches} - {""}): self.types.addItem(kind(k), k)
        for k in sorted({b.get("status", "") for b in batches} - {""}): self.states.addItem(status(k), k)
        filters.addWidget(self.types); filters.addWidget(self.states); filters.addStretch()
        self.count = QLabel(); filters.addWidget(self.count); box.addLayout(filters)
        self.model = BatchModel(batches, self); self.proxy = BatchFilter(self); self.proxy.setSourceModel(self.model)
        self.table = QtWidgets.QTableView(); self.table.setModel(self.proxy)
        self.table.setItemDelegateForColumn(0,BatchNameDelegate(self.table)); self.table.setWordWrap(False)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True); self.table.setShowGrid(False)
        self.table.verticalHeader().hide(); self.table.verticalHeader().setDefaultSectionSize(max(44,self.fontMetrics().height()*2+10))
        header = self.table.horizontalHeader(); header.setSectionResizeMode(0, QtWidgets.QHeaderView.Stretch)
        header.setDefaultAlignment(QtCore.Qt.AlignLeft|QtCore.Qt.AlignVCenter)
        for i, width in [(1, 100), (2, 150), (3, 155)]: header.resizeSection(i, width)
        box.addWidget(self.table, 1)
        row = QtWidgets.QHBoxLayout(); row.addStretch()
        cancel = QPushButton(tr("Cancel")); cancel.clicked.connect(self.reject)
        self.open_button = QPushButton(tr("Open results")); self.open_button.setObjectName("primary"); self.open_button.setDefault(True)
        self.open_button.clicked.connect(self.accept_selection)
        row.addWidget(cancel); row.addWidget(self.open_button); box.addLayout(row)
        self.timer = QtCore.QTimer(self); self.timer.setSingleShot(True); self.timer.setInterval(120)
        self.timer.timeout.connect(self.filter_rows); self.search.textChanged.connect(self.timer.start)
        self.types.currentIndexChanged.connect(self.filter_rows); self.states.currentIndexChanged.connect(self.filter_rows)
        self.table.doubleClicked.connect(self.accept_selection)
        self.search.installEventFilter(self); self.table.installEventFilter(self)
        self.table.selectionModel().selectionChanged.connect(lambda *_: self.open_button.setEnabled(self.table.currentIndex().isValid()))
        self.filter_rows()
        if selected:
            for i, b in enumerate(self.model.rows):
                if b["id"] == selected:
                    self.table.setCurrentIndex(self.proxy.mapFromSource(self.model.index(i, 0))); break
        screen = self.screen().availableGeometry()
        self.resize(min(980, int(screen.width() * .9)), min(580, int(screen.height() * .7)))
        self.setMaximumHeight(int(screen.height() * .7))
        self.search.setFocus()

    def filter_rows(self, *_):
        self.proxy.query = self.search.text().strip().casefold()
        self.proxy.kind = self.types.currentData(); self.proxy.state = self.states.currentData()
        self.proxy.invalidateFilter()
        self.count.setText(f("{0} tasks", self.proxy.rowCount()))
        if self.proxy.rowCount(): self.table.setCurrentIndex(self.proxy.index(0, 0))
        self.open_button.setEnabled(self.proxy.rowCount() > 0)

    def accept_selection(self, *_):
        if self.timer.isActive(): self.timer.stop(); self.filter_rows()
        index = self.table.currentIndex()
        if index.isValid():
            self.selected_id = index.data(QtCore.Qt.UserRole); self.accept()

    def eventFilter(self, obj, event):
        if event.type() == QtCore.QEvent.KeyPress:
            if obj is self.search and event.key() in (QtCore.Qt.Key_Down, QtCore.Qt.Key_Up):
                if self.timer.isActive(): self.timer.stop(); self.filter_rows()
                self.table.setFocus(); return True
            if event.key() in (QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter):
                self.accept_selection(); return True
        return super().eventFilter(obj, event)
