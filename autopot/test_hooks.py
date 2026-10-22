"""測試用：把介面跳出來的對話框換成什麼都不做（測試時不能有視窗卡住等人按）。"""


def no_dialogs(monkeypatch, gui_module):
    mb = gui_module.messagebox
    for n in ("showinfo", "showwarning", "showerror"):
        monkeypatch.setattr(mb, n, lambda *a, **k: None)
    monkeypatch.setattr(mb, "askyesno", lambda *a, **k: True)
