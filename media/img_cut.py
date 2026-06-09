#!/usr/bin/env python3
"""
Image Editor - 图片裁切编辑器
支持文件夹拖入、缩略图浏览、多比例裁切、快速保存
"""

import sys
import os
from pathlib import Path

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QListWidget, QListWidgetItem, QLabel, QPushButton, QButtonGroup,
    QGraphicsView, QGraphicsScene, QGraphicsPixmapItem, QGraphicsRectItem,
    QSplitter, QFileDialog, QMessageBox, QSizePolicy, QToolBar, QFrame,
)
from PySide6.QtCore import (
    Qt, QRectF, QPointF, QSize, Signal, QMimeData,
)
from PySide6.QtGui import (
    QPixmap, QImage, QPainter, QPen, QBrush, QColor, QIcon,
    QDragEnterEvent, QDropEvent, QCursor, QAction,
)

IMAGE_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.bmp', '.gif', '.webp', '.tiff', '.tif'}

MATERIAL_STYLE = """
QMainWindow {
    background-color: #FAFAFA;
}
QToolBar {
    background-color: #FFFFFF;
    border-bottom: 1px solid #E0E0E0;
    padding: 4px 8px;
    spacing: 8px;
}
QListWidget {
    background-color: #FFFFFF;
    border: none;
    border-right: 1px solid #E0E0E0;
    outline: none;
    padding: 4px;
}
QListWidget::item {
    border-radius: 8px;
    padding: 4px;
    margin: 2px 4px;
}
QListWidget::item:selected {
    background-color: #E3F2FD;
}
QListWidget::item:hover {
    background-color: #F5F5F5;
}
QPushButton {
    background-color: #FFFFFF;
    color: #424242;
    border: 1px solid #E0E0E0;
    border-radius: 6px;
    padding: 8px 16px;
    font-size: 13px;
    font-weight: 500;
}
QPushButton:hover {
    background-color: #F5F5F5;
    border-color: #BDBDBD;
}
QPushButton:pressed {
    background-color: #EEEEEE;
}
QPushButton:checked {
    background-color: #1976D2;
    color: #FFFFFF;
    border-color: #1976D2;
}
QPushButton#saveBtn {
    background-color: #1976D2;
    color: #FFFFFF;
    border: none;
    padding: 8px 24px;
    font-size: 14px;
    font-weight: 600;
}
QPushButton#saveBtn:hover {
    background-color: #1565C0;
}
QPushButton#saveBtn:pressed {
    background-color: #0D47A1;
}
QPushButton#saveBtn:disabled {
    background-color: #BDBDBD;
    color: #FFFFFF;
}
QLabel#dropHint {
    color: #9E9E9E;
    font-size: 16px;
}
QLabel#titleLabel {
    font-size: 14px;
    font-weight: 600;
    color: #424242;
    padding: 8px 12px;
}
QLabel#infoLabel {
    color: #757575;
    font-size: 12px;
    padding: 4px 12px;
}
QFrame#cropToolbar {
    background-color: #FFFFFF;
    border: 1px solid #E0E0E0;
    border-radius: 8px;
    padding: 4px;
}
QGraphicsView {
    background-color: #F5F5F5;
    border: none;
}
QSplitter::handle {
    background-color: #E0E0E0;
    width: 1px;
}
"""


class CropOverlay(QGraphicsRectItem):
    """可拖拽调整的裁切框"""

    HANDLE_SIZE = 10

    def __init__(self, rect: QRectF, boundary: QRectF, aspect_ratio=None):
        super().__init__(rect)
        self.boundary = boundary
        self.aspect_ratio = aspect_ratio  # width / height, None = free
        self.setPen(QPen(QColor(25, 118, 210), 2, Qt.SolidLine))
        self.setBrush(QBrush(Qt.NoBrush))
        self.setFlags(
            QGraphicsRectItem.ItemIsMovable | QGraphicsRectItem.ItemSendsGeometryChanges
        )
        self.setAcceptHoverEvents(True)
        self._resize_handle = None  # which edge/corner is being dragged
        self._drag_start_rect = None
        self._drag_start_pos = None

    def _handle_at(self, pos: QPointF):
        r = self.rect()
        hs = self.HANDLE_SIZE
        regions = {
            'top-left': QRectF(r.left(), r.top(), hs, hs),
            'top-right': QRectF(r.right() - hs, r.top(), hs, hs),
            'bottom-left': QRectF(r.left(), r.bottom() - hs, hs, hs),
            'bottom-right': QRectF(r.right() - hs, r.bottom() - hs, hs, hs),
            'top': QRectF(r.left() + hs, r.top(), r.width() - 2 * hs, hs),
            'bottom': QRectF(r.left() + hs, r.bottom() - hs, r.width() - 2 * hs, hs),
            'left': QRectF(r.left(), r.top() + hs, hs, r.height() - 2 * hs),
            'right': QRectF(r.right() - hs, r.top() + hs, hs, r.height() - 2 * hs),
        }
        for name, region in regions.items():
            if region.contains(pos):
                return name
        return None

    def hoverMoveEvent(self, event):
        handle = self._handle_at(event.pos())
        cursors = {
            'top-left': Qt.SizeFDiagCursor,
            'bottom-right': Qt.SizeFDiagCursor,
            'top-right': Qt.SizeBDiagCursor,
            'bottom-left': Qt.SizeBDiagCursor,
            'top': Qt.SizeVerCursor,
            'bottom': Qt.SizeVerCursor,
            'left': Qt.SizeHorCursor,
            'right': Qt.SizeHorCursor,
        }
        if handle:
            self.setCursor(cursors.get(handle, Qt.ArrowCursor))
        else:
            self.setCursor(Qt.SizeAllCursor)
        super().hoverMoveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._resize_handle = self._handle_at(event.pos())
            self._drag_start_rect = self.rect()
            self._drag_start_pos = event.pos()
            if self._resize_handle:
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._resize_handle and self._drag_start_rect:
            self._do_resize(event.pos())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._resize_handle = None
        self._drag_start_rect = None
        self._drag_start_pos = None
        super().mouseReleaseEvent(event)

    def _do_resize(self, pos: QPointF):
        r = QRectF(self._drag_start_rect)
        dx = pos.x() - self._drag_start_pos.x()
        dy = pos.y() - self._drag_start_pos.y()
        handle = self._resize_handle
        min_size = 30

        if 'right' in handle:
            r.setRight(max(r.left() + min_size, r.right() + dx))
        if 'left' in handle:
            r.setLeft(min(r.right() - min_size, r.left() + dx))
        if 'bottom' in handle:
            r.setBottom(max(r.top() + min_size, r.bottom() + dy))
        if 'top' in handle:
            r.setTop(min(r.bottom() - min_size, r.top() + dy))

        if self.aspect_ratio:
            w, h = r.width(), r.height()
            if 'left' in handle or 'right' in handle:
                h = w / self.aspect_ratio
                if 'top' in handle:
                    r.setTop(r.bottom() - h)
                else:
                    r.setBottom(r.top() + h)
            else:
                w = h * self.aspect_ratio
                if 'left' in handle:
                    r.setLeft(r.right() - w)
                else:
                    r.setRight(r.left() + w)

        # clamp to boundary (in item coordinates, boundary is at parent scene)
        r = r.intersected(QRectF(0, 0, self.boundary.width(), self.boundary.height()))
        if r.width() >= min_size and r.height() >= min_size:
            self.setRect(r)

    def itemChange(self, change, value):
        if change == QGraphicsRectItem.ItemPositionChange and self.boundary:
            new_pos = value
            r = self.rect()
            bx = self.boundary.x()
            by = self.boundary.y()
            bw = self.boundary.width()
            bh = self.boundary.height()
            x = max(bx, min(new_pos.x(), bx + bw - r.width()))
            y = max(by, min(new_pos.y(), by + bh - r.height()))
            return QPointF(x, y)
        return super().itemChange(change, value)

    def paint(self, painter, option, widget=None):
        r = self.rect()
        # 半透明遮罩
        full = QRectF(0, 0, self.boundary.width(), self.boundary.height())
        # 将crop rect转到scene坐标
        crop_scene = r.translated(self.pos())

        painter.save()
        # 画遮罩
        mask_color = QColor(0, 0, 0, 100)
        # top
        painter.fillRect(QRectF(
            -self.pos().x(), -self.pos().y(),
            full.width(), crop_scene.top()
        ), mask_color)
        # bottom
        painter.fillRect(QRectF(
            -self.pos().x(), crop_scene.bottom() - self.pos().y(),
            full.width(), full.height() - crop_scene.bottom()
        ), mask_color)
        # left
        painter.fillRect(QRectF(
            -self.pos().x(), r.top(),
            crop_scene.left(), r.height()
        ), mask_color)
        # right
        painter.fillRect(QRectF(
            crop_scene.right() - self.pos().x(), r.top(),
            full.width() - crop_scene.right(), r.height()
        ), mask_color)
        painter.restore()

        # 裁切框
        pen = QPen(QColor(25, 118, 210), 2)
        painter.setPen(pen)
        painter.drawRect(r)

        # 三等分线
        pen2 = QPen(QColor(25, 118, 210, 100), 1, Qt.DashLine)
        painter.setPen(pen2)
        w3 = r.width() / 3
        h3 = r.height() / 3
        for i in range(1, 3):
            painter.drawLine(
                QPointF(r.left() + w3 * i, r.top()),
                QPointF(r.left() + w3 * i, r.bottom()),
            )
            painter.drawLine(
                QPointF(r.left(), r.top() + h3 * i),
                QPointF(r.right(), r.top() + h3 * i),
            )

        # 四角手柄
        hs = self.HANDLE_SIZE
        handle_brush = QBrush(QColor(25, 118, 210))
        painter.setBrush(handle_brush)
        painter.setPen(Qt.NoPen)
        corners = [
            QRectF(r.left(), r.top(), hs, hs),
            QRectF(r.right() - hs, r.top(), hs, hs),
            QRectF(r.left(), r.bottom() - hs, hs, hs),
            QRectF(r.right() - hs, r.bottom() - hs, hs, hs),
        ]
        for c in corners:
            painter.drawRect(c)
        painter.setBrush(Qt.NoBrush)


class ImageCanvas(QGraphicsView):
    """图片预览编辑区"""

    crop_changed = Signal(QRectF)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        self.setDragMode(QGraphicsView.NoDrag)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setAlignment(Qt.AlignCenter)

        self._pixmap_item = None
        self._crop_overlay = None
        self._original_pixmap = None
        self._aspect_ratio = None  # None = free

    def load_image(self, path: str):
        self._scene.clear()
        self._crop_overlay = None
        pix = QPixmap(path)
        if pix.isNull():
            return
        self._original_pixmap = pix
        self._pixmap_item = QGraphicsPixmapItem(pix)
        self._scene.addItem(self._pixmap_item)
        self._scene.setSceneRect(QRectF(pix.rect()))
        self.fitInView(self._scene.sceneRect(), Qt.KeepAspectRatio)
        self._add_crop_overlay()

    def set_aspect_ratio(self, ratio):
        """ratio: float or None for free"""
        self._aspect_ratio = ratio
        if self._original_pixmap:
            self._add_crop_overlay()

    def _add_crop_overlay(self):
        if self._crop_overlay:
            self._scene.removeItem(self._crop_overlay)
            self._crop_overlay = None
        if not self._original_pixmap:
            return

        pw = self._original_pixmap.width()
        ph = self._original_pixmap.height()
        boundary = QRectF(0, 0, pw, ph)

        if self._aspect_ratio:
            ar = self._aspect_ratio
            if pw / ph > ar:
                ch = ph * 0.8
                cw = ch * ar
            else:
                cw = pw * 0.8
                ch = cw / ar
        else:
            cw = pw * 0.8
            ch = ph * 0.8

        cx = (pw - cw) / 2
        cy = (ph - ch) / 2
        crop_rect = QRectF(0, 0, cw, ch)

        self._crop_overlay = CropOverlay(crop_rect, boundary, self._aspect_ratio)
        self._crop_overlay.setPos(cx, cy)
        self._scene.addItem(self._crop_overlay)

    def get_crop_rect(self):
        if not self._crop_overlay:
            return None
        r = self._crop_overlay.rect()
        pos = self._crop_overlay.pos()
        return QRectF(
            r.x() + pos.x(), r.y() + pos.y(),
            r.width(), r.height()
        )

    def get_original_pixmap(self):
        return self._original_pixmap

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._scene.sceneRect().width() > 0:
            self.fitInView(self._scene.sceneRect(), Qt.KeepAspectRatio)
            # Update crop overlay when view size changes
            self._update_view_on_resize()

    def _update_view_on_resize(self):
        """Ensure proper scaling and crop overlay update on resize"""
        if self._crop_overlay and self._original_pixmap:
            # Re-fit the view to maintain proper aspect ratio
            self.fitInView(self._scene.sceneRect(), Qt.KeepAspectRatio)


class ThumbnailList(QListWidget):
    """左侧缩略图列表，支持拖入文件夹"""

    folder_dropped = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setIconSize(QSize(120, 90))
        self.setSpacing(4)
        self.setMinimumWidth(140)
        # Remove maximum width constraint to allow splitter resizing
        self.setViewMode(QListWidget.IconMode)
        self.setResizeMode(QListWidget.Adjust)
        self.setMovement(QListWidget.Static)
        self.setFlow(QListWidget.TopToBottom)
        self.setWrapping(False)
        self.setWordWrap(True)
        self.setUniformItemSizes(False)

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent):
        urls = event.mimeData().urls()
        for url in urls:
            path = url.toLocalFile()
            if os.path.isdir(path):
                self.folder_dropped.emit(path)
                return
            elif os.path.isfile(path):
                parent = os.path.dirname(path)
                self.folder_dropped.emit(parent)
                return


class ImageEditor(QMainWindow):
    """主窗口"""

    def __init__(self):
        super().__init__()
        self.setWindowTitle('Image Editor')
        self.setMinimumSize(1000, 680)
        self.resize(1200, 780)

        self._current_path = None
        self._image_paths = []

        self._init_ui()
        self.setStyleSheet(MATERIAL_STYLE)
        self.setAcceptDrops(True)

    def _init_ui(self):
        # 中心组件
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QHBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # === 左侧面板 ===
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(0)

        title = QLabel('图片列表')
        title.setObjectName('titleLabel')
        left_layout.addWidget(title)

        self._thumb_list = ThumbnailList()
        self._thumb_list.folder_dropped.connect(self._load_folder)
        self._thumb_list.currentRowChanged.connect(self._on_thumb_selected)
        left_layout.addWidget(self._thumb_list)

        self._info_label = QLabel('拖入文件夹以加载图片')
        self._info_label.setObjectName('infoLabel')
        left_layout.addWidget(self._info_label)

        open_btn = QPushButton('打开文件夹')
        open_btn.clicked.connect(self._open_folder_dialog)
        open_btn.setFixedHeight(36)
        left_layout.addWidget(open_btn)

        # === 右侧面板 ===
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(0)

        # 裁切工具栏
        crop_bar = QFrame()
        crop_bar.setObjectName('cropToolbar')
        crop_bar_layout = QHBoxLayout(crop_bar)
        crop_bar_layout.setContentsMargins(8, 6, 8, 6)
        crop_bar_layout.setSpacing(6)

        crop_label = QLabel('裁切比例:')
        crop_label.setStyleSheet('color: #616161; font-size: 13px; font-weight: 500;')
        crop_bar_layout.addWidget(crop_label)

        self._ratio_group = QButtonGroup(self)
        self._ratio_group.setExclusive(True)
        ratios = [
            ('自由', None),
            ('正方形', 1.0),
            ('4:3', 4 / 3),
            ('3:4', 3 / 4),
            ('16:9', 16 / 9),
            ('原始', 'original'),
        ]
        for i, (name, ratio) in enumerate(ratios):
            btn = QPushButton(name)
            btn.setCheckable(True)
            btn.setFixedHeight(32)
            if i == 0:
                btn.setChecked(True)
            self._ratio_group.addButton(btn, i)
            crop_bar_layout.addWidget(btn)
            btn.ratio_value = ratio

        self._ratio_group.idClicked.connect(self._on_ratio_changed)

        crop_bar_layout.addStretch()

        # 保存按钮
        self._save_btn = QPushButton('保存裁切')
        self._save_btn.setObjectName('saveBtn')
        self._save_btn.setFixedHeight(36)
        self._save_btn.setEnabled(False)
        self._save_btn.clicked.connect(self._save_cropped)
        crop_bar_layout.addWidget(self._save_btn)

        right_layout.addWidget(crop_bar)

        # 图片画布
        self._canvas = ImageCanvas()
        right_layout.addWidget(self._canvas)

        # 状态信息
        self._status_label = QLabel('')
        self._status_label.setObjectName('infoLabel')
        self._status_label.setAlignment(Qt.AlignRight)
        right_layout.addWidget(self._status_label)

        # 拖入提示（无图片时显示）
        self._drop_hint = QLabel('将文件夹拖入此处\n或使用左侧「打开文件夹」按钮')
        self._drop_hint.setObjectName('dropHint')
        self._drop_hint.setAlignment(Qt.AlignCenter)

        # splitter with proper resize behavior
        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(left_panel)
        splitter.addWidget(right_panel)
        splitter.setSizes([200, 800])
        # Allow both panels to resize proportionally
        splitter.setStretchFactor(0, 0)  # Left panel: fixed minimum, can grow
        splitter.setStretchFactor(1, 2)  # Right panel: gets priority on stretch
        # Connect to handle resize events
        splitter.splitterMoved.connect(self._on_splitter_moved)

        main_layout.addWidget(splitter)

    def _on_splitter_moved(self, pos, index):
        """Handle splitter movement to update thumbnail icon size"""
        # Update thumbnail sizes based on available width
        left_width = self._thumb_list.width()
        if left_width > 180:
            icon_size = QSize(140, 105)
        elif left_width > 140:
            icon_size = QSize(120, 90)
        else:
            icon_size = QSize(100, 75)
        self._thumb_list.setIconSize(icon_size)
        
        # Force canvas to re-fit the image
        if self._canvas._scene.sceneRect().width() > 0:
            self._canvas.fitInView(self._canvas._scene.sceneRect(), Qt.KeepAspectRatio)

    def _open_folder_dialog(self):
        folder = QFileDialog.getExistingDirectory(self, '选择图片文件夹')
        if folder:
            self._load_folder(folder)

    def _load_folder(self, folder: str):
        self._thumb_list.clear()
        self._image_paths.clear()

        files = sorted(Path(folder).iterdir())
        for f in files:
            if f.suffix.lower() in IMAGE_EXTENSIONS and f.is_file():
                self._image_paths.append(str(f))

        if not self._image_paths:
            self._info_label.setText('未找到图片文件')
            return

        for img_path in self._image_paths:
            pix = QPixmap(img_path)
            if pix.isNull():
                continue
            thumb = pix.scaled(QSize(120, 90), Qt.KeepAspectRatio, Qt.SmoothTransformation)
            item = QListWidgetItem()
            item.setIcon(QIcon(thumb))
            item.setText(Path(img_path).name)
            item.setData(Qt.UserRole, img_path)
            item.setSizeHint(QSize(140, 110))
            self._thumb_list.addItem(item)

        self._info_label.setText(f'{len(self._image_paths)} 张图片')
        if self._thumb_list.count() > 0:
            self._thumb_list.setCurrentRow(0)

    def _on_thumb_selected(self, row):
        if row < 0 or row >= self._thumb_list.count():
            return
        item = self._thumb_list.item(row)
        path = item.data(Qt.UserRole)
        self._current_path = path
        self._canvas.load_image(path)
        self._save_btn.setEnabled(True)

        pix = self._canvas.get_original_pixmap()
        if pix:
            self._status_label.setText(
                f'{Path(path).name}  |  {pix.width()} × {pix.height()}'
            )

        # 应用当前比例
        self._apply_current_ratio()

    def _on_ratio_changed(self, id_):
        self._apply_current_ratio()

    def _apply_current_ratio(self):
        btn = self._ratio_group.checkedButton()
        if not btn:
            return
        ratio = btn.ratio_value
        if ratio == 'original':
            pix = self._canvas.get_original_pixmap()
            if pix and pix.height() > 0:
                ratio = pix.width() / pix.height()
            else:
                ratio = None
        self._canvas.set_aspect_ratio(ratio)

    def _save_cropped(self):
        if not self._current_path:
            return
        crop_rect = self._canvas.get_crop_rect()
        pix = self._canvas.get_original_pixmap()
        if not crop_rect or not pix:
            return

        x = max(0, int(crop_rect.x()))
        y = max(0, int(crop_rect.y()))
        w = min(int(crop_rect.width()), pix.width() - x)
        h = min(int(crop_rect.height()), pix.height() - y)

        if w <= 0 or h <= 0:
            QMessageBox.warning(self, '错误', '裁切区域无效')
            return

        cropped = pix.copy(x, y, w, h)

        # 生成保存路径: 原路径_宽x高.ext
        p = Path(self._current_path)
        save_name = f'{p.stem}_{w}x{h}{p.suffix}'
        save_path = p.parent / save_name

        if cropped.save(str(save_path)):
            self._status_label.setText(f'已保存: {save_name}')
        else:
            QMessageBox.warning(self, '保存失败', f'无法保存至 {save_path}')

    # 主窗口也接受拖入
    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent):
        urls = event.mimeData().urls()
        for url in urls:
            path = url.toLocalFile()
            if os.path.isdir(path):
                self._load_folder(path)
                return
            elif os.path.isfile(path):
                self._load_folder(os.path.dirname(path))
                return


def main():
    app = QApplication(sys.argv)
    app.setStyle('Fusion')
    editor = ImageEditor()
    editor.show()
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
