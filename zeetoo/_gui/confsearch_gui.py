import tkinter as tk
from zeetoo import confsearch


class AppFrame(tk.Frame):
    def __init__(self, master):
        super().__init__(master)
        tk.Label(self, text="This module is not available yet.").pack()
