# editMyPdf Lite

A lightweight, local PDF editor: upload a PDF, click any line of text to
edit or delete it, add new text anywhere, delete pages, then save a new
PDF. Everything runs on your machine — nothing is uploaded anywhere else.

## How it works

- **Backend**: Flask (Python). No database — files and edit history live
  in memory / on disk for the life of the process.
- **Text extraction**: `pdfplumber` reads each line of text on a page
  (position, font, size, color).
- **Editing**: when you change a line, the app draws a white rectangle
  over the original text and writes the new text on top using
  `reportlab`, then merges that onto the real page with `pypdf`. This is
  the same "redact + overlay" technique most lightweight PDF editors use.
- **Preview rendering**: `pypdfium2` rasterizes each page (with edits
  baked in) to a PNG so the browser can show a crisp, accurate preview.
- **Saving**: all edits are flattened into a fresh PDF you can download.

Fonts are mapped to the closest of the 14 standard PDF fonts
(Helvetica / Times / Courier, regular/bold/italic), since those are the
only fonts guaranteed to render anywhere without embedding font files.
Because of this, heavily stylized or embedded custom fonts may render in
a close approximation rather than pixel-for-pixel identical.

## Setup

```bash
cd pdfeditor
python3 -m venv venv        # optional but recommended
source venv/bin/activate
pip install -r requirements.txt
```

## Run

```bash
python3 app.py
```

Then open **http://127.0.0.1:5000** in your browser.

## Using it

1. Drop a PDF onto the upload screen (or click to choose one).
2. Click any line of text to edit it inline; press Enter to apply,
   Escape to cancel.
3. Hover a line and click the small **×** to delete just that line.
4. Click **Add text** in the top bar, then click anywhere on the page to
   place a new text box.
5. Hover a page thumbnail on the left and click **×** to mark a whole
   page for deletion (click again to restore it).
6. Use **Undo** to step back through changes, or **Reset** to discard
   everything since upload.
7. Click **Save & download** to flatten all changes into a new PDF and
   download it.

## Notes / limitations

- This is intentionally a lightweight line-level text editor (like the
  reference site), not a full PDF authoring tool — it edits existing
  text and lets you add plain text boxes; it doesn't do rich formatting,
  images, or vector graphics editing.
- Uploaded files and their edit history are kept in the server process's
  memory and in `uploads/` / `outputs/` on disk. Restarting the server
  clears in-progress sessions (this is a local single-user tool, not a
  hosted multi-user service).
- Max upload size is 60 MB by default (edit `MAX_CONTENT_LENGTH` in
  `app.py` to change it).
