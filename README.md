# PDF Tool

A simple vibecoded pdf tool to perform following actions with pdf:

1. Merge PDF files
2. Split a PDF
3. Remove pages
4. Rotate pages
5. Extract or reorder pages
6. Show PDF information
7. Compress a PDF

Publishing it to always have it at my disposal when needed.

## Usage

1. Clone the pository
2. Create a virtual environment:

```bash
python3 -m venv .venv
```

3. Activate the virtual environment:

```bash
# On Windows:
./.venv/Scripts/activate

# On Mac/Linux:
source .venv/bin/activate
```

4. Install the requirements:

```bash
python3 -m pip install -r requirements.txt
```

5. Install [Ghostscript](https://www.ghostscript.com/releases/gsdnld.html) and make
   `gswin64c` (Windows) or `gs` (macOS/Linux) available on your `PATH`. Ghostscript
   is used to downsample and recompress images when compressing PDFs.

6. Run the PDF tool:

```bash
python3 pdf_tool.py
```
