
import json
import os
import re
import unicodedata
import shutil
import threading
import tkinter as tk
from tkinter import messagebox, scrolledtext
from pathlib import Path

import requests
import arabic_reshaper
from bidi.algorithm import get_display



def safe_calculate(expression):
    """Evaluate basic arithmetic without eval or arbitrary code execution."""
    expression = expression.translate(str.maketrans("??????????", "0123456789"))
    tree = ast.parse(expression, mode="eval")
    ops = {
        ast.Add: operator.add, ast.Sub: operator.sub,
        ast.Mult: operator.mul, ast.Div: operator.truediv,
        ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod,
        ast.USub: operator.neg, ast.UAdd: operator.pos,
    }
    def visit(node):
        if isinstance(node, ast.Expression):
            return visit(node.body)
        if isinstance(node, ast.Constant) and type(node.value) in (int, float):
            return node.value
        if isinstance(node, ast.BinOp) and type(node.op) in ops:
            a, b = visit(node.left), visit(node.right)
            if abs(a) > 10**12 or abs(b) > 10**12:
                raise ValueError("number too large")
            return ops[type(node.op)](a, b)
        if isinstance(node, ast.UnaryOp) and type(node.op) in ops:
            return ops[type(node.op)](visit(node.operand))
        raise ValueError("unsupported expression")
    return visit(tree)

def display_arabic(text):
    if not isinstance(text, str) or not text:
        return text

    try:
        result = []
        for paragraph in text.split("\n"):
            if not paragraph.strip():
                result.append("")
                continue

            words = paragraph.split()
            current = ""
            for word in words:
                candidate = current + " " + word if current else word
                if current and len(candidate) > 45:
                    result.append(current)
                    current = word
                else:
                    current = candidate

            if current:
                result.append(current)

        return "\n".join(result)

    except Exception as exc:
        print("Arabic display error:", exc)
        return text


MODEL = "iraq-ai"
OLLAMA_URL = "http://127.0.0.1:11437/api/chat"
REQUEST_TIMEOUT = 240
MAX_HISTORY_MESSAGES = 30
MAX_RESPONSE_TOKENS = 800
MAX_RETRIES = 2

APP_DATA_DIR = Path(
    os.getenv("LOCALAPPDATA", str(Path.home()))
) / "IRAQ AI"

APP_DATA_DIR.mkdir(parents=True, exist_ok=True)

MEMORY_FILE = APP_DATA_DIR / "chat_history.json"
OLD_MEMORY_FILE = Path(__file__).with_name("chat_history.json")

if not MEMORY_FILE.exists() and OLD_MEMORY_FILE.exists():
    try:
        shutil.copy2(OLD_MEMORY_FILE, MEMORY_FILE)
    except OSError:
        pass

BG = "#090B14"
PANEL = "#111421"
PANEL_LIGHT = "#191D30"
PURPLE = "#7C3AED"
PURPLE_HOVER = "#6D28D9"
TEXT = "#F4F4FA"
MUTED = "#A6AAC3"
BORDER = "#292D46"

SYSTEM_PROMPT = """
You are IRAQ AI, an intelligent, reliable, and helpful assistant.

CORE RULES:
- Answer the exact question the user asks.
- Understand the latest user message and use conversation history only when relevant.
- Match the language of the latest message. Use natural Iraqi Arabic for Iraqi Arabic,
  clear Modern Standard Arabic for formal Arabic, and natural English for English.
- Be accurate, logical, clear, and useful. Avoid repetition and unnecessary filler.
- Never invent facts, quotations, sources, statistics, or capabilities.
- If uncertain, clearly say what is uncertain and distinguish facts from estimates.
- Correct false assumptions politely and explain why when useful.
- For complex tasks, reason carefully before answering, but provide the conclusion
  and a clear explanation rather than private internal reasoning.
- For technical tasks, give practical steps, safe commands, and explain important risks.
- For code changes, preserve existing behavior unless a change is requested.
- Ask a clarifying question only when missing information materially affects the answer.
- Do not claim to have browsed the internet, run code, or verified something unless true.
- Adapt answer length to the task: concise for simple questions, detailed for complex ones.
- Use readable formatting when it makes the answer easier to understand.

Your goal is to provide the most helpful, honest, and relevant answer possible.

# IRAQ_AI_NATURAL_REPLY_RULE
IMPORTANT RESPONSE STYLE:
- Do not mention the current year or date in greetings or unrelated answers.
- Only state the current year when the user explicitly asks about the year or date.
- For a greeting such as hello or ???, reply with a natural short greeting.
- Do not repeat facts from earlier conversation unless relevant to the user's latest question.
""".strip()


def load_history():
    try:
        with open(MEMORY_FILE, "r", encoding="utf-8") as file:
            data = json.load(file)

        if isinstance(data, list):
            return [
                item for item in data
                if isinstance(item, dict)
                and item.get("role") in ("user", "assistant")
                and isinstance(item.get("content"), str)
            ]
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass

    return []


def save_history(history):
    try:
        with open(MEMORY_FILE, "w", encoding="utf-8") as file:
            json.dump(
                history,
                file,
                ensure_ascii=False,
                indent=2
            )
        return True
    except OSError:
        return False


def clean_answer(answer):
    if not isinstance(answer, str):
        return ""

    answer = re.sub(
        r"<think\b[^>]*>.*?</think\s*>",
        "",
        answer,
        flags=re.DOTALL | re.IGNORECASE
    )

    answer = re.sub(
        r"<think\b[^>]*>.*$",
        "",
        answer,
        flags=re.DOTALL | re.IGNORECASE
    )

    answer = re.sub(
        r"<\|[^|]*\|>",
        "",
        answer
    )

    answer = re.sub(
        r"^\s*(?:assistant|IRAQ AI)\s*:\s*",
        "",
        answer,
        flags=re.IGNORECASE
    )

    answer = re.sub(
        r"^\s*(?:analysis|thinking)\s*\.{0,3}\s*",
        "",
        answer,
        flags=re.IGNORECASE
    )

    answer = re.sub(
        r"\s*(?:\.\.\.done thinking\.{0,3}|"
        r"<\/?think>)\s*",
        "",
        answer,
        flags=re.IGNORECASE
    )

    return answer.strip()


def contains_reasoning_trace(answer):
    if not answer:
        return True

    patterns = [
        r"^\s*(?:Okay|Alright),?\s+the user\b",
        r"^\s*The user is asking\b",
        r"^\s*The user wants\b",
        r"^\s*Let me (?:check|think|confirm|make sure)\b",
        r"^\s*I need to\b",
        r"^\s*I should\b",
        r"^\s*First,\s",
        r"^\s*Wait,\s",
        r"^\s*Thinking\s*\.{0,3}\s*$",
        r"\bI need to provide a clear",
        r"\bI should present the answer",
        r"\bLet me structure it properly",
        r"\bPutting it all together\b",
        r"\bthe answer should be\b",
        r"\bI need to make sure I provide\b"
    ]

    return any(
        re.search(
            pattern,
            answer,
            re.IGNORECASE | re.MULTILINE
        )
        for pattern in patterns
    )


class IraqAIApp:
    def __init__(self, root):
        self.root = root
        self.root.title("IRAQ AI - Local Assistant")
        self.root.geometry("550x400")
        self.root.minsize(450, 350)
        self.root.configure(bg=BG)

        self.history = load_history()
        self.busy = False
        self.history_lock = threading.Lock()

        self.build_layout()
        self.render_history()
        self.entry.focus_set()

    def build_layout(self):
        # IRAQ_AI_FULL_WINDOW_BACKGROUND
        from PIL import Image, ImageTk, ImageEnhance

        self._bg_source = Image.open(
            str(Path(__file__).parent / "iraq_ai_iraqi_banner.png")
        ).convert("RGB")
        self._bg_canvas = tk.Canvas(
            self.root, bd=0, highlightthickness=0, bg=BG
        )
        # تحميل وتعديل حجم الصورة لتناسب الارتفاع 45 بدقة وتجنب الامتداد
        from PIL import Image, ImageTk
        img_path = Path(__file__).parent / "iraq_ai_iraqi_banner.jpg"
        if img_path.exists():
            orig_img = Image.open(img_path)
            t_height = 45
            t_width = int(t_height * (orig_img.width / orig_img.height))
            resized_img = orig_img.resize((t_width, t_height), Image.Resampling.LANCZOS)
            self._banner_photo = ImageTk.PhotoImage(resized_img)
            self._bg_canvas.config(height=45)
            self._bg_canvas.create_image(0, 0, anchor="nw", image=self._banner_photo)
        self._bg_canvas.tk.call("lower", self._bg_canvas._w)
        self._bg_photo = None
        self._bg_item = self._bg_canvas.create_image(
            0, 0, anchor="nw"
        )

        def _resize_full_background(event):
            if event.width < 2 or event.height < 2:
                return
            try:
                from PIL import Image, ImageTk
                source = self._bg_source
                ratio = max(
                    event.width / source.width,
                    event.height / source.height
                )
                size = (
                    max(1, round(source.width * ratio)),
                    max(1, round(source.height * ratio))
                )
                resized = source.resize(size, Image.Resampling.LANCZOS)
                left = max(0, (resized.width - event.width) // 2)
                top = max(0, (resized.height - event.height) // 2)
                resized = resized.crop((
                    left, top, left + event.width, top + event.height
                ))
                self._bg_photo = ImageTk.PhotoImage(resized)
                self._bg_canvas.itemconfigure(
                    self._bg_item, image=self._bg_photo
                )
                self._bg_canvas.coords(self._bg_item, 0, 0)
            except Exception as exc:
                print("Background resize error:", exc)

        self._bg_canvas.bind("<Configure>", _resize_full_background)
        self.content = tk.Frame(self.root, bg=BG)
        self.content.pack(
            side="top", fill="both", expand=True, pady=(45, 0)
        )

        self.sidebar = tk.Frame(
            self.content,
            bg=PANEL,
            width=225
        )
        self.sidebar.pack(side="left", fill="y")
        self.sidebar.pack_propagate(False)

        brand = tk.Frame(self.sidebar, bg=PANEL)
        brand.pack(fill="x", padx=18, pady=(24, 20))

        tk.Label(
            brand,
            text="Ã¢Å“Â¦",
            font=("Segoe UI Symbol", 34, "bold"),
            fg="#60A5FA",
            bg=PANEL
        ).pack(anchor="w")

        tk.Label(
            brand,
            text="IRAQ AI",
            font=("Segoe UI", 22, "bold"),
            fg=TEXT,
            bg=PANEL
        ).pack(anchor="w", pady=(3, 0))

        tk.Label(
            brand,
            text="Your local AI assistant",
            font=("Segoe UI", 9),
            fg=MUTED,
            bg=PANEL
        ).pack(anchor="w", pady=(2, 0))

        self.make_button(
            self.sidebar,
            "+   New Chat",
            self.new_chat,
            bg=PURPLE,
            hover=PURPLE_HOVER
        ).pack(fill="x", padx=14, pady=(12, 8))

        self.make_button(
            self.sidebar,
            "Copy Selected Text",
            self.copy_selected
        ).pack(fill="x", padx=14, pady=5)

        self.make_button(
            self.sidebar,
            "Conversation Memory",
            self.show_memory
        ).pack(fill="x", padx=14, pady=5)

        self.make_button(
            self.sidebar,
            "About IRAQ AI",
            self.show_about
        ).pack(fill="x", padx=14, pady=5)

        # IRAQ_AI_LANGUAGE_TOGGLE
        self.language_button = self.make_button(
            self.sidebar,
            "Language: English",
            self.toggle_language
        )
        self.language_button.pack(fill="x", padx=14, pady=5)

        bottom = tk.Frame(self.sidebar, bg=PANEL)
        bottom.pack(
            side="bottom",
            fill="x",
            padx=16,
            pady=18
        )

        tk.Frame(
            bottom,
            bg=BORDER,
            height=1
        ).pack(fill="x", pady=(0, 12))

        tk.Label(
            bottom,
            text="Ã¢â€”Â  LOCAL MODEL",
            font=("Segoe UI", 9, "bold"),
            fg="#34D399",
            bg=PANEL
        ).pack(anchor="w")

        tk.Label(
            bottom,
            text=MODEL,
            font=("Segoe UI", 9),
            fg=MUTED,
            bg=PANEL
        ).pack(anchor="w", pady=(4, 0))

        self.main = tk.Frame(self.content, bg=BG)
        self.main.pack(side="left", fill="both", expand=True)
        # IRAQ_AI_PANEL_BACKGROUND_FINAL
        def _install_panel_background(panel):
            from PIL import Image, ImageTk, ImageEnhance

            canvas = tk.Canvas(
                panel, bd=0, highlightthickness=0, bg=BG
            )
            canvas.place(x=0, y=0, relwidth=1, relheight=1)
            canvas.tk.call("lower", canvas._w)

            state = {"photo": None, "item": None}

            def _redraw(event=None):
                width = panel.winfo_width()
                height = panel.winfo_height()
                if width < 2 or height < 2:
                    return

                resized = self._bg_source.resize(
                    (width, height), Image.Resampling.LANCZOS
                )
                from PIL import ImageEnhance
                resized = ImageEnhance.Contrast(resized).enhance(1.12)
                resized = ImageEnhance.Sharpness(resized).enhance(1.5)  # IRAQ_AI_BACKGROUND_QUALITY_FIX
                photo = ImageTk.PhotoImage(resized)
                state["photo"] = photo

                if state["item"] is None:
                    state["item"] = canvas.create_image(
                        0, 0, anchor="nw", image=photo
                    )
                else:
                    canvas.itemconfigure(state["item"], image=photo)

                canvas.tk.call("lower", canvas._w)

            panel.bind("<Configure>", _redraw, add="+")
            panel.after(150, _redraw)

        # _install_panel_background(self.sidebar)
        # _install_panel_background(self.main)

        header = tk.Frame(
            self.main,
            bg=PANEL,
            height=76
        )
        header.pack(fill="x", padx=18, pady=(16, 0))
        header.pack_propagate(False)

        header_text = tk.Frame(header, bg=PANEL)
        header_text.pack(side="left", padx=22, pady=14)

        tk.Label(
            header_text,
            text="IRAQ AI",
            font=("Segoe UI", 17, "bold"),
            fg=TEXT,
            bg=PANEL
        ).pack(anchor="w")

        tk.Label(
            header_text,
            text="Local AI | Clear Arabic",
            font=("Segoe UI", 9),
            fg=MUTED,
            bg=PANEL
        ).pack(anchor="w", pady=(2, 0))

        self.status = tk.Label(
            header,
            text="Ã¢â€”Â  Ready",
            font=("Segoe UI", 10, "bold"),
            fg="#34D399",
            bg=PANEL
        )
        self.status.pack(side="right", padx=22)

        self.chat = scrolledtext.ScrolledText(
            self.main,
            wrap="word",
            state="disabled",
            bg=BG,
            fg=TEXT,
            insertbackground=TEXT,
            selectbackground=PURPLE,
            relief="flat",
            borderwidth=0,
            padx=22,
            pady=22,
            font=("Segoe UI", 11),
            spacing1=4,
            spacing3=8
        )
        # IRAQ_AI_COPY_SELECTION_FIX
        def _copy_chat_selection(event=None):
            try:
                selected = self.chat.get("sel.first", "sel.last")
                self.clipboard_clear()
                self.clipboard_append(selected)
                self.update()
            except Exception:
                pass
            return "break"

        self.chat.bind("<Control-c>", _copy_chat_selection)
        self.chat.bind("<Control-C>", _copy_chat_selection)

        self.chat.pack(
            fill="both",
            expand=True,
            padx=18,
            pady=14
        )

        # IRAQ_AI_COPY_LAST_ANSWER_BUTTON
        self.copy_button_frame = tk.Frame(self.main)
        self.copy_button_frame.pack(
            side="bottom", fill="x", padx=18, pady=(0, 8)
        )
        self.copy_answer_button = tk.Button(
            self.copy_button_frame,
            text="COPY CHAT",
            command=self.copy_last_answer,
            cursor="hand2"
        )
        self.copy_answer_button.pack(side="right")

        self.chat.tag_configure(
            "user_name",
            foreground="#93C5FD",
            font=("Segoe UI", 10, "bold"),
            spacing1=12
        )

        self.chat.tag_configure(
            "bot_name",
            foreground="#C4B5FD",
            font=("Segoe UI", 10, "bold"),
            spacing1=12
        )

        self.chat.tag_configure(
            "body",
            foreground=TEXT,
            font=("Segoe UI", 11),
            lmargin1=10,
            lmargin2=10,
            rmargin=10,
            spacing1=3,
            spacing3=8
        )

        self.chat.tag_configure(
            "notice",
            foreground=MUTED,
            font=("Segoe UI", 10),
            justify="center",
            spacing1=10,
            spacing3=10
        )

        composer = tk.Frame(
            self.main,
            bg=PANEL,
            padx=16,
            pady=14
        )
        composer.pack(fill="x", padx=18, pady=(0, 16))

        entry_frame = tk.Frame(
            composer,
            bg=PANEL_LIGHT,
            highlightbackground=BORDER,
            highlightthickness=1
        )
        entry_frame.pack(fill="x")

        self.entry = tk.Text(
            entry_frame,
            height=2,
            wrap="word",
            bg=PANEL_LIGHT,
            fg=TEXT,
            insertbackground=TEXT,
            relief="flat",
            font=("Segoe UI", 11),
            padx=12,
            pady=10,
            selectbackground=PURPLE
        )
        self.entry.pack(
            side="left",
            fill="both",
            expand=True
        )
        self.entry.bind("<Control-Return>", self.send_event)
        # IRAQ_AI_ENTER_SEND_FIX
        self.entry.bind("<Return>", self.send_event)
        self.entry.bind(
            "<Shift-Return>",
            lambda event: (self.entry.insert("insert", "\n"), "break")[1]
        )

        # IRAQ_AI_CLIPBOARD_FIX
        self.entry.bind(
            "<Control-v>",
            lambda event: (self.entry.event_generate("<<Paste>>"), "break")[1]
        )
        self.entry.bind(
            "<Control-V>",
            lambda event: (self.entry.event_generate("<<Paste>>"), "break")[1]
        )
        self.entry.bind(
            "<Shift-Insert>",
            lambda event: (self.entry.event_generate("<<Paste>>"), "break")[1]
        )
        self.entry.bind(
            "<Control-c>",
            lambda event: (self.entry.event_generate("<<Copy>>"), "break")[1]
        )
        self.entry.bind(
            "<Control-C>",
            lambda event: (self.entry.event_generate("<<Copy>>"), "break")[1]
        )
        self.entry.bind(
            "<Control-x>",
            lambda event: (self.entry.event_generate("<<Cut>>"), "break")[1]
        )
        self.entry.bind(
            "<Control-a>",
            lambda event: (self.entry.tag_add("sel", "1.0", "end-1c"), "break")[1]
        )
        self.entry.bind(
            "<Control-A>",
            lambda event: (self.entry.tag_add("sel", "1.0", "end-1c"), "break")[1]
        )

        self.clipboard_menu = tk.Menu(self.root, tearoff=0)
        self.clipboard_menu.add_command(
            label="Cut",
            command=lambda: self.entry.event_generate("<<Cut>>")
        )
        self.clipboard_menu.add_command(
            label="Copy",
            command=lambda: self.entry.event_generate("<<Copy>>")
        )
        self.clipboard_menu.add_command(
            label="Paste",
            command=lambda: self.entry.event_generate("<<Paste>>")
        )
        self.clipboard_menu.add_separator()
        self.clipboard_menu.add_command(
            label="Select All",
            command=lambda: self.entry.tag_add("sel", "1.0", "end-1c")
        )

        self.entry.bind(
            "<Button-3>",
            lambda event: (
                self.clipboard_menu.tk_popup(event.x_root, event.y_root),
                self.clipboard_menu.grab_release(),
                "break"
            )[-1]
        )

        self.send_button = tk.Button(
            entry_frame,
            text="Send  Ã¢Å¾Â¤",
            command=self.send_message,
            bg=PURPLE,
            activebackground=PURPLE_HOVER,
            fg="white",
            activeforeground="white",
            relief="flat",
            borderwidth=0,
            font=("Segoe UI", 10, "bold"),
            padx=18,
            pady=12,
            cursor="hand2"
        )
        self.send_button.pack(
            side="right",
            padx=8,
            pady=8
        )

        tk.Label(
            composer,
            text="IRAQ AI runs locally with Ollama",
            bg=PANEL,
            fg=MUTED,
            font=("Segoe UI", 8)
        ).pack(pady=(8, 0))

    # IRAQ_AI_LANGUAGE_TOGGLE
    def toggle_language(self):
        self.ui_language = getattr(self, "ui_language", "en")
        self.ui_language = "ar" if self.ui_language == "en" else "en"

        en_ar = {
            "Your local AI assistant": "\u0645\u0633\u0627\u0639\u062f\u0643 \u0627\u0644\u0630\u0643\u064a \u0627\u0644\u0645\u062d\u0644\u064a",
            "+   New Chat": "+   \u0645\u062d\u0627\u062f\u062b\u0629 \u062c\u062f\u064a\u062f\u0629",
            "Copy Selected Text": "\u0646\u0633\u062e \u0627\u0644\u0646\u0635 \u0627\u0644\u0645\u062d\u062f\u062f",
            "Conversation Memory": "\u0630\u0627\u0643\u0631\u0629 \u0627\u0644\u0645\u062d\u0627\u062f\u062b\u0629",
            "About IRAQ AI": "\u062d\u0648\u0644 IRAQ AI",
            "Local AI | Clear Arabic": "\u0630\u0643\u0627\u0621 \u0645\u062d\u0644\u064a | \u0639\u0631\u0628\u064a \u0648\u0627\u0636\u062d",
            "Ready": "\u062c\u0627\u0647\u0632",
            "Thinking...": "\u062c\u0627\u0631\u064a \u0627\u0644\u062a\u0641\u0643\u064a\u0631...",
            "COPY CHAT": "\u0646\u0633\u062e \u0627\u0644\u0645\u062d\u0627\u062f\u062b\u0629",
            "Send": "\u0625\u0631\u0633\u0627\u0644",
            "IRAQ AI runs locally with Ollama": "\u064a\u0639\u0645\u0644 IRAQ AI \u0645\u062d\u0644\u064a\u0627\u064b \u0639\u0628\u0631 Ollama",
            "Language: English": "\u0627\u0644\u0644\u063a\u0629: \u0627\u0644\u0639\u0631\u0628\u064a\u0629",
            "Language: ???????": "Language: English",
        }
        ar_en = {v: k for k, v in en_ar.items()}
        translations = en_ar if self.ui_language == "ar" else ar_en

        def update_widgets(parent):
            for widget in parent.winfo_children():
                try:
                    old = widget.cget("text")
                    if old in translations:
                        widget.configure(text=translations[old])
                except (tk.TclError, TypeError):
                    pass
                update_widgets(widget)

        update_widgets(self.root)
        try:
            self.root.title(
                "IRAQ AI - ??????? ??????"
                if self.ui_language == "ar"
                else "IRAQ AI - Local Assistant"
            )
            self.language_button.configure(
                text="\u0627\u0644\u0644\u063a\u0629: English"
                if self.ui_language == "ar"
                else "Language: English"
            )
            if not self.busy:
                self.status.configure(
                    text="\u25cf \u062c\u0627\u0647\u0632"
                    if self.ui_language == "ar"
                    else "\u25cf Ready"
                )
        except tk.TclError:
            pass

    def make_button(
        self,
        parent,
        label,
        command,
        bg=PANEL_LIGHT,
        hover="#242943"
    ):
        button = tk.Button(
            parent,
            text=label,
            command=command,
            anchor="w",
            bg=bg,
            activebackground=hover,
            fg=TEXT,
            activeforeground=TEXT,
            relief="flat",
            borderwidth=0,
            font=("Segoe UI", 10),
            padx=13,
            pady=12,
            cursor="hand2"
        )

        button.bind(
            "<Enter>",
            lambda event: button.configure(bg=hover)
        )

        button.bind(
            "<Leave>",
            lambda event: button.configure(bg=bg)
        )

        return button

    def copy_last_answer(self):
        try:
            content = self.chat.get("1.0", "end-1c").strip()
            if not content:
                messagebox.showinfo("IRAQ AI", "Chat is empty.")
                return

            # Copy the visible conversation text as displayed.
            self.clipboard_clear()
            self.clipboard_append(content)
            self.update()
            messagebox.showinfo(
                "IRAQ AI",
                "Conversation text copied. Paste it here with Ctrl+V."
            )
        except Exception as exc:
            messagebox.showerror("Copy error", str(exc))

    def append_message(self, role, content):
        self.chat.configure(state="normal")

        if role == "user":
            self.chat.insert("end", "YOU\n", "user_name")
        else:
            self.chat.insert("end", "IRAQ AI\n", "bot_name")

        self.chat.insert(
            "end",
            display_arabic(content.strip()) + "\n\n",
            "body"
        )

        self.chat.configure(state="disabled")
        self.chat.see("end")

    def render_history(self):
        self.chat.configure(state="normal")
        self.chat.delete("1.0", "end")

        if not self.history:
            self.chat.insert(
                "end",
                "Welcome to IRAQ AI\n\nSend a message to start chatting.",
                "notice"
            )
        else:
            for item in self.history:
                if item["role"] == "user":
                    tag = "user_name"
                    name = "YOU\n"
                else:
                    tag = "bot_name"
                    name = "IRAQ AI\n"

                self.chat.insert("end", name, tag)
                self.chat.insert(
                    "end",
                    display_arabic(item["content"].strip()) + "\n\n",
                    "body"
                )

        self.chat.configure(state="disabled")
        self.chat.see("end")

    def send_event(self, event):
        self.send_message()
        return "break"

    def send_message(self):

        # IRAQ_AI_DIRECT_YEAR_ANSWER
        import datetime
        _year_text = self.entry.get("1.0", "end").strip().casefold()
        _year_questions = (
            "\u0646\u062d\u0646 \u0641\u064a \u0627\u064a \u0633\u0646\u0629",
            "\u0646\u062d\u0646 \u0641\u064a \u0623\u064a \u0633\u0646\u0629",
            "\u0646\u062d\u0646 \u0641\u064a \u0627\u064a \u0639\u0627\u0645",
            "\u0627\u062d\u0646\u0627 \u0628\u0627\u064a \u0633\u0646\u0629",
            "\u0634\u0646\u0648 \u0627\u0644\u0633\u0646\u0629 \u0627\u0644\u062d\u0627\u0644\u064a\u0629",
            "\u0627\u064a \u0633\u0646\u0629 \u0627\u062d\u0646\u0627",
            "\u0627\u064a \u0639\u0627\u0645 \u0627\u062d\u0646\u0627",
            "what year", "current year"
        )
        if any(k in _year_text for k in _year_questions):
            _answer = "\u0646\u062d\u0646 \u0641\u064a \u0639\u0627\u0645 " + str(datetime.datetime.now().year) + " \u0645\u064a\u0644\u0627\u062f\u064a\u0629."
            self.entry.delete("1.0", "end")
            self.append_message("user", _year_text)
            self.append_message("assistant", _answer)
            with self.history_lock:
                self.history.extend([
                    {"role": "user", "content": _year_text},
                    {"role": "assistant", "content": _answer}
                ])
                save_history(self.history)
            return

        if self.busy:
            return

        message = self.entry.get("1.0", "end").strip()

        if not message:
            return

        self.entry.delete("1.0", "end")

        with self.history_lock:
            self.history.append(
                {"role": "user", "content": message}
            )
            save_history(self.history)
            request_history = list(
                self.history[-MAX_HISTORY_MESSAGES:]
            )

        self.append_message("user", message)

        self.busy = True

        self.send_button.configure(
            state="disabled",
            text="Thinking..."
        )

        self.status.configure(
            text="Ã¢â€”Â  Thinking...",
            fg="#FBBF24"
        )

        thread = threading.Thread(
            target=self.ask_ollama,
            args=(request_history,),
            daemon=True
        )
        thread.start()

    def ask_ollama(self, request_history):
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT}
        ]
        messages[0]["content"] += (
            "\\n\\nCURRENT DATE: "
            + __import__("datetime").date.today().isoformat()
            + ". The current year is "
            + str(__import__("datetime").date.today().year)
            + ". For questions about today's date or year, use this current date."
        )

        # Clean old language instructions from copied history, not saved history.
        cleaned_history = []
        for item in request_history:
            copied = dict(item)
            content = str(copied.get("content", ""))

            if copied.get("role") == "user":
                content = re.sub(
                    r"\n\nReply ONLY in natural (?:Iraqi Arabic|English)\..*$",
                    "",
                    content,
                    flags=re.DOTALL
                )

            copied["content"] = content
            cleaned_history.append(copied)

        messages.extend(cleaned_history)

        latest_user_message = next(
            (
                item.get("content", "")
                for item in reversed(cleaned_history)
                if item.get("role") == "user"
            ),
            ""
        )

        # Reliable arithmetic for explicitly priced Arabic shopping problems.
        normalized_calc = unicodedata.normalize("NFKC", latest_user_message)
        normalized_calc = normalized_calc.translate(str.maketrans(
            "٠١٢٣٤٥٦٧٨٩", "0123456789"
        ))
        if "سعر" in normalized_calc and "دفع" in normalized_calc:
            notebook_qty = re.search(r"(\d+)\s*دفاتر?", normalized_calc)
            notebook_price = re.search(r"سعر\s*الدفتر\s*(\d+)", normalized_calc)
            pen_qty = re.search(r"(\d+)\s*أقلام?", normalized_calc)
            two_pens = re.search(r"قلمين", normalized_calc)
            pen_price = re.search(r"سعر\s*القلم\s*(\d+)", normalized_calc)
            paid = re.search(r"دفع\s*(\d+)", normalized_calc)

            if notebook_qty and notebook_price and pen_price and paid and (pen_qty or two_pens):
                n = int(notebook_qty.group(1))
                np = int(notebook_price.group(1))
                q = int(pen_qty.group(1)) if pen_qty else 2
                pp = int(pen_price.group(1))
                total_paid = int(paid.group(1))
                total_cost = n * np + q * pp
                change = total_paid - total_cost

                if 0 <= change and n > 0 and q > 0:
                    result = f"المجموع: {total_cost} دينار. الباقي: {change} دينار."
                    self.root.after(0, self.finish_reply, result, None)
                    return
        # Evaluate standalone arithmetic expressions safely.
        expression = unicodedata.normalize("NFKC", latest_user_message)
        expression = "".join(
            str(unicodedata.decimal(ch)) if ch.isdecimal() else ch
            for ch in expression
        ).strip()
        if re.fullmatch(r"[\d\s()+\-*/%.]+", expression) and re.search(r"[+\-*/%]", expression):
            try:
                if len(expression) <= 200:
                    value = safe_calculate(expression)
                    if isinstance(value, (int, float)) and abs(value) < 10**15:
                        if isinstance(value, float) and value.is_integer():
                            value = int(value)
                        result = str(value)
                        self.root.after(0, self.finish_reply, result, None)
                        return
            except (ValueError, SyntaxError, ZeroDivisionError, TypeError, OverflowError):
                pass
        # Detect the language of the latest user message.
        normalized_message = unicodedata.normalize(
            "NFKC", latest_user_message
        )

        arabic_count = sum(
            "\u0600" <= char <= "\u06FF"
            for char in normalized_message
        )
        english_count = sum(
            "a" <= char.lower() <= "z"
            for char in normalized_message
        )

        if arabic_count > english_count:
            language_instruction = (
                "The latest user message is Arabic. "
                "Answer in Arabic, matching the user's style."
            )
        elif english_count > 0:
            language_instruction = (
                "The latest user message is English. "
                "Answer in English. Do not answer in Arabic."
            )
        else:
            language_instruction = (
                "Match the language of the latest user message."
            )

        messages[0]["content"] += "\n\n" + language_instruction

        # Enforce the detected language next to the latest user question.
        for _item in reversed(messages):
            if _item.get("role") == "user":
                _item["content"] = (
                    str(_item.get("content", ""))
                    + "\n\n"
                    + language_instruction
                    + " This language rule is mandatory for this reply."
                )
                break
        messages[0]["content"] += """

For arithmetic and word problems:
- Identify every quantity, its unit price, and the requested result.
- Calculate each item separately before combining totals.
- Apply addition, subtraction, multiplication, and division in the correct order.
- Check the final result against the original question.
- Never guess a numerical result. If essential information is missing, ask for clarification.
- Give the direct answer in the required language. Do not reveal internal reasoning.
"""

        try:
            answer = ""

            for attempt in range(MAX_RETRIES):
                current_messages = list(messages)

                if attempt > 0:
                    current_messages.append({
                        "role": "user",
                        "content": (
                            "Give only the final answer to the previous question. "
                            "Do not include thoughts, analysis, planning, "
                            "or explanations of your reasoning. "
                            "Follow the language instruction in the system message."
                        )
                    })

                payload = {
                    "model": MODEL,
                    "messages": current_messages,
                    "stream": False,
                    "think": False,
                    "options": {
                        "temperature": 0.2,
                        "top_p": 0.8,
                        "top_k": 30,
                        "repeat_penalty": 1.1,
                        "num_predict": MAX_RESPONSE_TOKENS,
                        "num_ctx": 8192
                    }
                }

                response = requests.post(
                    OLLAMA_URL,
                    json=payload,
                    timeout=REQUEST_TIMEOUT
                )
                response.raise_for_status()

                data = response.json()
                message_data = data.get("message", {})

                raw_answer = message_data.get("content", "")
                thinking_text = message_data.get("thinking", "")

                answer = clean_answer(raw_answer)
                # IRAQ_AI_GREETING_POSTPROCESS
                _last_user = next(
                    (str(item.get("content", "")).strip().casefold()
                     for item in reversed(request_history)
                     if isinstance(item, dict) and item.get("role") == "user"),
                    ""
                )
                _greetings = {
                    "\u0647\u0644\u0627",
                    "\u0647\u0644\u0648",
                    "\u0645\u0631\u062d\u0628\u0627",
                    "\u0627\u0647\u0644\u0627",
                    "\u0647\u0627\u064a",
                    "hello", "hi", "hey"
                }
                if _last_user.strip("!?. \t") in _greetings:
                    answer = "\u0647\u0644\u0627 \u0628\u064a\u0643! \u0634\u0644\u0648\u0646 \u0623\u0643\u062f\u0631 \u0623\u0633\u0627\u0639\u062f\u0643\u061f"
                    thinking_text = ""


                if (
                    answer
                    and not thinking_text.strip()
                    and not contains_reasoning_trace(answer)
                ):
                    break

                answer = ""

            if not answer:
                answer = (
                    "Ã™â€žÃ™â€¦ Ã˜Â£Ã˜ÂªÃ™â€¦Ã™Æ’Ã™â€  Ã™â€¦Ã™â€  Ã˜Â¥Ã™â€ Ã˜Â´Ã˜Â§Ã˜Â¡ Ã˜Â¥Ã˜Â¬Ã˜Â§Ã˜Â¨Ã˜Â© Ã™Ë†Ã˜Â§Ã˜Â¶Ã˜Â­Ã˜Â© Ã™â€¡Ã˜Â°Ã™â€¡ Ã˜Â§Ã™â€žÃ™â€¦Ã˜Â±Ã˜Â©. "
                    "Ã™Å Ã˜Â±Ã˜Â¬Ã™â€° Ã˜Â¥Ã˜Â¹Ã˜Â§Ã˜Â¯Ã˜Â© Ã˜ÂµÃ™Å Ã˜Â§Ã˜ÂºÃ˜Â© Ã˜Â§Ã™â€žÃ˜Â³Ã˜Â¤Ã˜Â§Ã™â€ž Ã™Ë†Ã˜Â§Ã™â€žÃ™â€¦Ã˜Â­Ã˜Â§Ã™Ë†Ã™â€žÃ˜Â© Ã™â€¦Ã˜Â¬Ã˜Â¯Ã˜Â¯Ã™â€¹Ã˜Â§."
                )

            self.root.after(
                0,
                self.finish_reply,
                answer,
                None
            )

        except requests.exceptions.ConnectionError:
            self.root.after(
                0,
                self.finish_reply,
                None,
                "Could not connect to Ollama. Make sure Ollama is running."
            )

        except requests.exceptions.Timeout:
            self.root.after(
                0,
                self.finish_reply,
                None,
                "The model took too long to respond. Please try again."
            )

        except requests.RequestException as error:
            self.root.after(
                0,
                self.finish_reply,
                None,
                f"Ollama request failed: {error}"
            )

        except (KeyError, ValueError, TypeError, AttributeError) as error:
            self.root.after(
                0,
                self.finish_reply,
                None,
                f"Could not read the model response: {error}"
            )

        except Exception as error:
            self.root.after(
                0,
                self.finish_reply,
                None,
                f"Unexpected error in IRAQ AI: {type(error).__name__}: {error}"
            )

    def finish_reply(self, answer, error):
        self.busy = False

        self.send_button.configure(
            state="normal",
            text="Send  Ã¢Å¾Â¤"
        )

        self.status.configure(
            text="Ã¢â€”Â  Ready",
            fg="#34D399"
        )

        if error:
            self.append_message("assistant", error)
            return

        with self.history_lock:
            self.history.append(
                {"role": "assistant", "content": answer}
            )
            save_history(self.history)

        self.append_message("assistant", answer)

    def new_chat(self):
        if self.busy:
            messagebox.showinfo(
                "Please Wait",
                "Wait for the current reply to finish."
            )
            return

        if not messagebox.askyesno(
            "New Chat",
            "Start a new conversation? The saved conversation history will be cleared."
        ):
            return

        with self.history_lock:
            self.history = []
            save_history(self.history)

        self.render_history()

    def copy_selected(self):
        try:
            selected = self.chat.get("sel.first", "sel.last")

            self.root.clipboard_clear()
            self.root.clipboard_append(selected)

            self.status.configure(
                text="Ã¢â€”Â  Copied",
                fg="#34D399"
            )

        except tk.TclError:
            messagebox.showinfo(
                "Copy Text",
                "Select some text in the chat first."
            )

    def show_memory(self):
        with self.history_lock:
            message_count = len(self.history)

        messagebox.showinfo(
            "Conversation Memory",
            f"Messages saved: {message_count}\n\n"
            f"Memory file:\n{MEMORY_FILE}"
        )

    def show_about(self):
        messagebox.showinfo(
            "About IRAQ AI",
            "IRAQ AI is a local desktop assistant powered by Ollama and Qwen3 8B.\n\n"
            "Your chat history is saved on this computer."
        )


if __name__ == "__main__":
    root = tk.Tk()
    app = IraqAIApp(root)
    root.mainloop()
