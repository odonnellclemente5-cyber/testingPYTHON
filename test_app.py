import tkinter as tk
from tkinter import scrolledtext

class ChatApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Local Test Chat")
        self.root.geometry("400x500")
        self.root.configure(bg="#1e1e1e")

        # --- Chat History Display Area ---
        self.chat_display = scrolledtext.ScrolledText(root, wrap=tk.WORD, bg="#252526", fg="#dcdcdc", font=("Arial", 10))
        self.chat_display.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        self.chat_display.config(state=tk.DISABLED)

        # Welcome message from "App"
        self.append_message("System: Hi! Type a message below and hit Send.")

        # --- Input Frame (Bottom) ---
        input_frame = tk.Frame(root, bg="#1e1e1e")
        input_frame.pack(fill=tk.X, padx=10, pady=10)

        # Text Input Box
        self.msg_entry = tk.Entry(input_frame, bg="#3c3c3c", fg="white", insertbackground="white", font=("Arial", 11))
        self.msg_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5))
        self.msg_entry.bind("<Return>", lambda event: self.send_message()) # Allow pressing Enter to send

        # Send Button
        self.send_btn = tk.Button(input_frame, text="Send", command=self.send_message, bg="#007acc", fg="white", font=("Arial", 10, "bold"), relief=tk.FLAT, padx=10)
        self.send_btn.pack(side=tk.RIGHT)

    def append_message(self, message):
        self.chat_display.config(state=tk.NORMAL)
        self.chat_display.insert(tk.END, message + "\n\n")
        self.chat_display.config(state=tk.DISABLED)
        self.chat_display.see(tk.END) # Auto-scroll to the bottom

    def send_message(self):
        user_text = self.msg_entry.get().strip()
        if not user_text:
            return
        
        # Display user message
        self.append_message(f"You: {user_text}")
        self.msg_entry.delete(0, tk.END)

        # Simple automated app response simulation
        response = f"App: I received your message: '{user_text}'"
        self.append_message(response)

if __name__ == "__main__":
    root = tk.Tk()
    app = ChatApp(root)
    root.mainloop()
