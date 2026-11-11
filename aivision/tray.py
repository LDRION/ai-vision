"""
Иконка в системном трее (правый нижний угол Windows) + меню ключевых действий.
Закрытие окна сворачивает лаунчер в трей, а не завершает его. Меню: Показать,
Свернуть, Выход. Иконка рисуется PIL — бирюзовый кружок с галочкой-пульсом.

    tray = Tray(app).start()     # app — окно лаунчера (ctk.CTk)
    ...
    tray.stop()
"""
import threading


def _icon_image():
    from PIL import Image, ImageDraw
    img = Image.new('RGBA', (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse([3, 3, 61, 61], fill=(16, 185, 129, 255))                 # бирюзовый круг (accent)
    d.line([(18, 33), (28, 44), (47, 21)], fill=(255, 255, 255, 255), width=7, joint='curve')
    return img


class Tray:
    def __init__(self, app, title: str = 'AI Vision'):
        self.app = app
        self.title = title
        self._icon = None
        self._thread = None

    def _tk(self, fn):
        try:
            self.app.after(0, fn)
        except Exception:
            pass

    def _show(self, *_):
        self._tk(lambda: (self.app.deiconify(), self.app.lift(), self.app.focus_force()))

    def _hide(self, *_):
        self._tk(self.app.withdraw)

    def _quit(self, *_):
        if self._icon:
            self._icon.stop()
        self._tk(self.app._real_quit)

    def start(self):
        try:
            from pystray import Icon, Menu, MenuItem
        except ImportError:
            return self                                            # нет pystray — без трея, не падаем
        menu = Menu(
            MenuItem('Показать', self._show, default=True),
            MenuItem('Свернуть', self._hide),
            Menu.SEPARATOR,
            MenuItem('Выход', self._quit),
        )
        self._icon = Icon('aivision', _icon_image(), self.title, menu)
        self._thread = threading.Thread(target=self._icon.run, daemon=True)
        self._thread.start()
        return self

    def stop(self):
        if self._icon:
            self._icon.stop()
            self._icon = None
