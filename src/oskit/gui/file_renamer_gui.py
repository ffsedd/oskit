# src/oskit/gui/file_renamer_gui.py
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from oskit.file_renamer import parse_mapping, batch_rename, undo_rename

class FileRenamerGUI(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("OSKit File Renamer")
        self.geometry("600x400")
        self.folder_path = tk.StringVar()
        self.log_path = tk.StringVar(value="rename_log.json")
        self.rules = []

        self.create_widgets()

    def create_widgets(self):
        # Folder selection
        folder_frame = ttk.Frame(self)
        folder_frame.pack(fill="x", padx=10, pady=5)
        ttk.Label(folder_frame, text="Target folder:").pack(side="left")
        ttk.Entry(folder_frame, textvariable=self.folder_path, width=40).pack(side="left", padx=5)
        ttk.Button(folder_frame, text="Browse", command=self.browse_folder).pack(side="left")

        # Rules
        rules_frame = ttk.Frame(self)
        rules_frame.pack(fill="x", padx=10, pady=5)
        ttk.Label(rules_frame, text="Rename rules (old:new, comma-separated):").pack(side="left")
        self.rules_entry = ttk.Entry(rules_frame, width=40)
        self.rules_entry.pack(side="left", padx=5)

        # Options
        options_frame = ttk.Frame(self)
        options_frame.pack(fill="x", padx=10, pady=5)
        self.recursive_var = tk.BooleanVar()
        self.apply_var = tk.BooleanVar()
        ttk.Checkbutton(options_frame, text="Recursive", variable=self.recursive_var).pack(side="left")
        ttk.Checkbutton(options_frame, text="Apply changes", variable=self.apply_var).pack(side="left")

        # Log path
        log_frame = ttk.Frame(self)
        log_frame.pack(fill="x", padx=10, pady=5)
        ttk.Label(log_frame, text="Log file:").pack(side="left")
        ttk.Entry(log_frame, textvariable=self.log_path, width=40).pack(side="left", padx=5)

        # Buttons
        buttons_frame = ttk.Frame(self)
        buttons_frame.pack(fill="x", padx=10, pady=10)
        ttk.Button(buttons_frame, text="Run", command=self.run_rename).pack(side="left", padx=5)
        ttk.Button(buttons_frame, text="Undo", command=self.run_undo).pack(side="left", padx=5)
        ttk.Button(buttons_frame, text="Quit", command=self.destroy).pack(side="right", padx=5)

        # Output box
        self.output_box = tk.Text(self, height=15)
        self.output_box.pack(fill="both", padx=10, pady=5, expand=True)
        self.output_box.config(state="disabled")

    def browse_folder(self):
        folder = filedialog.askdirectory()
        if folder:
            self.folder_path.set(folder)

    def print_output(self, text):
        self.output_box.config(state="normal")
        self.output_box.insert("end", text + "\n")
        self.output_box.see("end")
        self.output_box.update()
        self.output_box.config(state="disabled")

    def run_rename(self):
        folder = self.folder_path.get()
        rules_text = self.rules_entry.get()
        if not folder or not rules_text:
            messagebox.showerror("Error", "Folder and rules are required")
            return

        rules_list = [r.strip() for r in rules_text.split(",")]
        rules = parse_mapping(rules_list)
        apply = self.apply_var.get()
        recursive = self.recursive_var.get()
        log_path = self.log_path.get()

        try:
            summary = batch_rename(
                folder=folder,
                rules=rules,
                apply=apply,
                recursive=recursive,
                log_path=log_path,
                verbose=False  # we capture output manually
            )

            # Print results in output box
            for src, dst in [(p["original"], p["renamed"]) for p in summary.get("plan", [])]:
                self.print_output(f"{src} -> {dst}")

            self.print_output("\nSummary:")
            self.print_output(f"  Dry run: {summary['dry_run']}")
            self.print_output(f"  Renamed: {summary['renamed']}")
            self.print_output(f"  Skipped: {summary['skipped']}")

        except Exception as e:
            messagebox.showerror("Error", str(e))

    def run_undo(self):
        log_file = self.log_path.get()
        if not log_file:
            messagebox.showerror("Error", "Log file is required for undo")
            return
        try:
            undo_rename(log_file, verbose=True)
        except Exception as e:
            messagebox.showerror("Error", str(e))


def main():
    app = FileRenamerGUI()
    app.mainloop()
    
    
if __name__ == '__main__':
	main()    
