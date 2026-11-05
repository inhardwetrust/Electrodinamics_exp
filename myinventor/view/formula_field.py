# formula_field.py
#
# A line edit for a formula of the parameters ("w / 2 + gap"):
#   - its value drawn at its right, in grey ("= 12.5"); a formula that does
#     not evaluate: a red frame, the error in its tooltip;
#   - the parameter names completed while typing: a popup of the names that
#     start with the word at the cursor (any case; "mycenter." offers its
#     fields, mycenter.x...); Enter / Tab / a click
#     puts the name in place of that word, and only that (the Enter that
#     takes a name does not finish the field: editingFinished waits).
# accepted() is whether the text evaluates. Qt only (no model but the
# formula evaluator).

import re

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QCompleter, QLineEdit

from model.expressions import ExpressionError, evaluate

WORD = re.compile(r"[A-Za-z_][A-Za-z_0-9]*(\.[A-Za-z_0-9]*)?$")       # a field too: "mycenter.x"
ERROR_STYLE = "QLineEdit { border: 1px solid #c81e1e; }"


class FormulaField(QLineEdit):
    def __init__(self, text, values, parent=None, unit=""):
        """values: the parameters' values ({name: float}); unit: shown after the value ("mm", "°")."""
        super().__init__(text, parent)
        self.values = dict(values)
        self.unit = unit
        self._shown = ""
        self._tip = ""
        self.completer_ = QCompleter(sorted(self.values, key=str.lower), self)      # its own model (a Python
        #   one passed in is collected)
        self.completer_.setCaseSensitivity(Qt.CaseInsensitive)
        self.completer_.setCompletionMode(QCompleter.PopupCompletion)
        self.completer_.setWidget(self)
        self.completer_.activated.connect(self.insert_name)
        self.textEdited.connect(self._complete)
        self.textChanged.connect(self._evaluate)
        self._evaluate()

    # ---- the value ----

    def value(self):
        """The value, or the error text (str)."""
        text = self.text().strip()
        if not text:
            return "empty"
        try:
            return float(evaluate(text, self.values))
        except ExpressionError as error:
            return str(error)
        except (ZeroDivisionError, OverflowError, ValueError) as error:
            return f"{text}: {error}"

    def accepted(self):
        return not isinstance(self.value(), str)

    def _evaluate(self):
        value = self.value()
        empty = not self.text().strip()
        if isinstance(value, str):
            self._shown = ""
            self.setStyleSheet("" if empty else ERROR_STYLE)
            QLineEdit.setToolTip(self, self._tip + ("" if empty else f"\nerror: {value}"))
        else:
            plain = re.fullmatch(r"\s*[-+]?[0-9.]+(e[-+]?[0-9]+)?\s*", self.text(), re.I) is not None
            self._shown = "" if plain else f"= {value:.6g}{(' ' + self.unit) if self.unit else ''}"
            self.setStyleSheet("")
            QLineEdit.setToolTip(self, self._tip)
        margin = self.fontMetrics().horizontalAdvance(self._shown) + 8 if self._shown else 0
        self.setTextMargins(0, 0, margin, 0)
        self.update()

    def setToolTip(self, text):
        """The field's own tip; an error is added below it while there is one."""
        self._tip = text
        self._evaluate()

    def shown_value(self):
        """The grey text at the right ("= 12.5 mm"), "" for a plain number or an error."""
        return self._shown

    def paintEvent(self, event):
        super().paintEvent(event)
        if self._shown:
            painter = QPainter(self)
            painter.setPen(QColor(128, 128, 128))
            rect = self.rect().adjusted(0, 0, -6, 0)
            painter.drawText(rect, Qt.AlignRight | Qt.AlignVCenter, self._shown)
            painter.end()

    # ---- completing names ----

    def _word(self):
        return WORD.search(self.text()[:self.cursorPosition()])

    def _complete(self):
        word = self._word()
        popup = self.completer_.popup()
        if word is None or not self.values:
            popup.hide()
            return
        self.completer_.setCompletionPrefix(word.group())
        if self.completer_.completionCount() == 0 or (self.completer_.completionCount() == 1
                                                      and self.completer_.currentCompletion() == word.group()):
            popup.hide()
            return
        rect = self.cursorRect()
        rect.setWidth(max(160, popup.sizeHintForColumn(0) + popup.verticalScrollBar().sizeHint().width()))
        self.completer_.complete(rect)

    def insert_name(self, name):
        """A completion taken: `name` in place of the word at the cursor."""
        word = self._word()
        pos = self.cursorPosition()
        start = pos - len(word.group()) if word else pos
        text = self.text()
        self.setText(text[:start] + name + text[pos:])
        self.setCursorPosition(start + len(name))

    def keyPressEvent(self, event):
        # The popup passes its keys on to the field first: the Enter / Tab that
        # takes a name must not finish (apply) the formula. Ignored, the popup
        # then takes the name (accepted, it would not).
        if event.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Tab) and self.completer_.popup().isVisible():
            event.ignore()
            return
        super().keyPressEvent(event)
