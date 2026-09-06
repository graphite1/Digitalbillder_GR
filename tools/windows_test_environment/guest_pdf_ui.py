"""Exercise the shipped invoice PDF renderer with a synthetic file and Tk canvas.

Runs only in the test Sandbox, imports the UI module without starting the app,
and never constructs an invoice window or opens an invoice database.
"""
import json
import os
from pathlib import Path
import sys
import tempfile
import tkinter as tk
from types import SimpleNamespace


def main():
    if os.environ.get("USERNAME") != "WDAGUtilityAccount":
        raise SystemExit("Only run in Windows Sandbox")
    root = Path(sys.executable).resolve().parent.parent
    if sys.executable.lower() != str(root / "runtime/python.exe").lower():
        raise SystemExit("Bundled Python required")
    sys.path.insert(0, str(root))
    import pymupdf
    from invoice_manager.ui.invoice_detail_window import InvoiceDetailWindow

    result = {"status": "failed", "scope": "shipped PDF renderer only; no invoice database"}
    window = tk.Tk()
    try:
        with tempfile.TemporaryDirectory(prefix="dbgr-pdf-ui-") as folder:
            pdf = Path(folder) / "synthetic.pdf"
            with pymupdf.open() as doc:
                doc.new_page(width=240, height=120).insert_text((20, 60), "Clean install PDF test")
                doc.save(pdf)
            canvas = tk.Canvas(window, width=400, height=200)
            canvas.pack()
            window.update()
            status = tk.StringVar(window)
            view = SimpleNamespace(pdf_path=str(pdf), pdf_page_index=0,
                                   pdf_zoom=1.0, pdf_pan_x=0, pdf_pan_y=0,
                                   pdf_canvas=canvas, pdf_status_var=status,
                                   render_pdf_marks=lambda: None,
                                   update_pdf_scrollregion=lambda: None,
                                   clear_pdf_preview=lambda message: status.set(message))
            InvoiceDetailWindow.render_pdf_page(view)
            window.update()
            images = [i for i in canvas.find_all() if canvas.type(i) == "image"]
            if not images or view.pdf_image.width() != 240:
                raise RuntimeError(status.get())
            result.update(status="pass", canvas_images=len(images), label=status.get(),
                          size=[view.pdf_image.width(), view.pdf_image.height()])
    except Exception as exc:
        result["error"] = str(exc)
    finally:
        window.destroy()
    Path("C:/DBGR-Results/pdf-ui-result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if result["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
