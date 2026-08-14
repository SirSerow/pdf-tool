"""A small interactive terminal utility for common PDF operations."""

from __future__ import annotations

import os
import sys
import tempfile
from contextlib import contextmanager
from getpass import getpass
from pathlib import Path
from typing import Iterator, Sequence

from pypdf import PdfReader, PdfWriter
from pypdf.errors import PdfReadError


APP_NAME = "PDF Tool"


def clean_path(value: str) -> Path:
    """Turn user input into a normalized path, accepting pasted quotes."""
    value = value.strip().strip('"').strip("'")
    return Path(value).expanduser().resolve()


def require_pdf(value: str) -> Path:
    path = clean_path(value)
    if not path.is_file():
        raise FileNotFoundError(f"File not found: {path}")
    if path.suffix.lower() != ".pdf":
        raise ValueError(f"Not a PDF file: {path}")
    return path


def output_path(value: str, default: Path) -> Path:
    path = clean_path(value) if value.strip() else default.resolve()
    if path.suffix.lower() != ".pdf":
        path = path.with_suffix(".pdf")
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def confirm(question: str, default: bool = False) -> bool:
    marker = "[Y/n]" if default else "[y/N]"
    answer = input(f"{question} {marker}: ").strip().lower()
    if not answer:
        return default
    return answer in {"y", "yes"}


def approve_outputs(paths: Sequence[Path]) -> bool:
    existing = [path for path in paths if path.exists()]
    if not existing:
        return True
    print(f"{len(existing)} output file(s) already exist.")
    return confirm("Overwrite them?")


def parse_pages(spec: str, page_count: int, *, allow_duplicates: bool = True) -> list[int]:
    """Parse one-based page text such as '1,3-5' into zero-based indexes."""
    spec = spec.strip().lower()
    if spec in {"all", "*"}:
        return list(range(page_count))
    if not spec:
        raise ValueError("Page selection cannot be empty.")

    pages: list[int] = []
    for raw_part in spec.split(","):
        part = raw_part.strip()
        if not part:
            raise ValueError("Invalid page selection: empty item.")
        if "-" in part:
            pieces = [piece.strip() for piece in part.split("-")]
            if len(pieces) != 2 or not all(piece.isdigit() for piece in pieces):
                raise ValueError(f"Invalid page range: {part!r}")
            start, end = map(int, pieces)
            step = 1 if end >= start else -1
            numbers = range(start, end + step, step)
        else:
            if not part.isdigit():
                raise ValueError(f"Invalid page number: {part!r}")
            numbers = [int(part)]

        for number in numbers:
            if not 1 <= number <= page_count:
                raise ValueError(
                    f"Page {number} is outside this document (1-{page_count})."
                )
            index = number - 1
            if allow_duplicates or index not in pages:
                pages.append(index)

    return pages


def safe_metadata(reader: PdfReader) -> dict[str, str]:
    if not reader.metadata:
        return {}
    return {
        str(key): str(value)
        for key, value in reader.metadata.items()
        if key and value is not None
    }


@contextmanager
def open_pdf(path: Path) -> Iterator[PdfReader]:
    """Open a PDF and request a password if it is encrypted."""
    with path.open("rb") as stream:
        reader = PdfReader(stream, strict=False)
        if reader.is_encrypted:
            password = getpass(f"Password for {path.name}: ")
            if reader.decrypt(password) == 0:
                raise PdfReadError(f"Incorrect password for {path.name}.")
        yield reader


def write_pdf(writer: PdfWriter, destination: Path, *, overwrite: bool) -> None:
    """Write through a temporary file so failed writes do not corrupt output."""
    if destination.exists() and not overwrite:
        raise FileExistsError(f"Output already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", suffix=".pdf", prefix=".pdf-tool-",
            dir=destination.parent, delete=False
        ) as temporary:
            temporary_name = temporary.name
            writer.write(temporary)
        os.replace(temporary_name, destination)
    except Exception:
        if temporary_name:
            Path(temporary_name).unlink(missing_ok=True)
        raise


def add_metadata(writer: PdfWriter, reader: PdfReader) -> None:
    metadata = safe_metadata(reader)
    if metadata:
        writer.add_metadata(metadata)


def merge_pdfs(sources: Sequence[Path], destination: Path, *, overwrite: bool) -> None:
    if len(sources) < 2:
        raise ValueError("Choose at least two PDF files to merge.")
    if any(source == destination for source in sources):
        raise ValueError("The output path cannot be one of the input files.")

    writer = PdfWriter()
    first_metadata: dict[str, str] = {}
    # PdfWriter clones pages, so each source can be closed after it is appended.
    for position, source in enumerate(sources):
        with open_pdf(source) as reader:
            if position == 0:
                first_metadata = safe_metadata(reader)
            for page in reader.pages:
                writer.add_page(page)
    if first_metadata:
        writer.add_metadata(first_metadata)
    write_pdf(writer, destination, overwrite=overwrite)


def split_pdf(source: Path, groups: Sequence[Sequence[int]], output_dir: Path,
              *, overwrite: bool) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    destinations = [
        output_dir / f"{source.stem}_part_{number:02d}.pdf"
        for number in range(1, len(groups) + 1)
    ]
    if any(path == source for path in destinations):
        raise ValueError("An output path would overwrite the input file.")

    with open_pdf(source) as reader:
        metadata = safe_metadata(reader)
        for group, destination in zip(groups, destinations):
            writer = PdfWriter()
            for index in group:
                writer.add_page(reader.pages[index])
            if metadata:
                writer.add_metadata(metadata)
            write_pdf(writer, destination, overwrite=overwrite)
    return destinations


def remove_pages(source: Path, pages_to_remove: Sequence[int], destination: Path,
                 *, overwrite: bool) -> int:
    if source == destination:
        raise ValueError("Choose a different output path; input files are never overwritten.")
    removed = set(pages_to_remove)
    with open_pdf(source) as reader:
        if len(removed) >= len(reader.pages):
            raise ValueError("Cannot remove every page from the document.")
        writer = PdfWriter()
        for index, page in enumerate(reader.pages):
            if index not in removed:
                writer.add_page(page)
        add_metadata(writer, reader)
        write_pdf(writer, destination, overwrite=overwrite)
        return len(writer.pages)


def rotate_pages(source: Path, pages_to_rotate: Sequence[int], angle: int,
                 destination: Path, *, overwrite: bool) -> None:
    if source == destination:
        raise ValueError("Choose a different output path; input files are never overwritten.")
    selected = set(pages_to_rotate)
    with open_pdf(source) as reader:
        writer = PdfWriter()
        for index, page in enumerate(reader.pages):
            writer.add_page(page)
            if index in selected:
                writer.pages[-1].rotate(angle)
        add_metadata(writer, reader)
        write_pdf(writer, destination, overwrite=overwrite)


def extract_pages(source: Path, selected_pages: Sequence[int], destination: Path,
                  *, overwrite: bool) -> None:
    if source == destination:
        raise ValueError("Choose a different output path; input files are never overwritten.")
    with open_pdf(source) as reader:
        writer = PdfWriter()
        for index in selected_pages:
            writer.add_page(reader.pages[index])
        add_metadata(writer, reader)
        write_pdf(writer, destination, overwrite=overwrite)


def collect_sources() -> list[Path]:
    print("Enter PDF paths in merge order. Press Enter on an empty line when done.")
    paths: list[Path] = []
    while True:
        raw = input(f"PDF {len(paths) + 1}: ").strip()
        if not raw:
            break
        paths.append(require_pdf(raw))
    return paths


def choose_input() -> Path:
    return require_pdf(input("Input PDF: "))


def choose_output(source: Path, suffix: str) -> Path:
    default = source.with_name(f"{source.stem}_{suffix}.pdf")
    raw = input(f"Output PDF [{default}]: ")
    return output_path(raw, default)


def get_page_count(source: Path) -> int:
    with open_pdf(source) as reader:
        return len(reader.pages)


def run_merge() -> None:
    sources = collect_sources()
    if len(sources) < 2:
        raise ValueError("Choose at least two PDF files.")
    default = sources[0].with_name("merged.pdf")
    destination = output_path(input(f"Output PDF [{default}]: "), default)
    overwrite = destination.exists() and confirm(f"Overwrite {destination}?")
    if destination.exists() and not overwrite:
        print("Cancelled.")
        return
    merge_pdfs(sources, destination, overwrite=overwrite)
    print(f"Created {destination}")


def run_split() -> None:
    source = choose_input()
    page_count = get_page_count(source)
    print(f"Document has {page_count} page(s).")
    print("Enter groups separated by semicolons (example: 1-3;4-6;7,9).")
    print("Leave empty to create one PDF per page.")
    spec = input("Groups: ").strip()
    groups = (
        [[index] for index in range(page_count)]
        if not spec
        else [parse_pages(group, page_count) for group in spec.split(";")]
    )
    if any(not group for group in groups):
        raise ValueError("Every split group must contain at least one page.")
    default_dir = source.with_name(f"{source.stem}_split")
    raw_dir = input(f"Output folder [{default_dir}]: ").strip()
    directory = clean_path(raw_dir) if raw_dir else default_dir.resolve()
    destinations = [
        directory / f"{source.stem}_part_{number:02d}.pdf"
        for number in range(1, len(groups) + 1)
    ]
    overwrite = any(path.exists() for path in destinations)
    if overwrite and not approve_outputs(destinations):
        print("Cancelled.")
        return
    created = split_pdf(source, groups, directory, overwrite=overwrite)
    print(f"Created {len(created)} file(s) in {directory}")


def run_remove() -> None:
    source = choose_input()
    page_count = get_page_count(source)
    print(f"Document has {page_count} page(s).")
    selected = parse_pages(input("Pages to remove (example: 2,5-7): "), page_count,
                           allow_duplicates=False)
    destination = choose_output(source, "pages_removed")
    overwrite = destination.exists() and confirm(f"Overwrite {destination}?")
    if destination.exists() and not overwrite:
        print("Cancelled.")
        return
    remaining = remove_pages(source, selected, destination, overwrite=overwrite)
    print(f"Created {destination} ({remaining} page(s) remain)")


def run_rotate() -> None:
    source = choose_input()
    page_count = get_page_count(source)
    print(f"Document has {page_count} page(s). Use 'all' to rotate every page.")
    selected = parse_pages(input("Pages to rotate: "), page_count,
                           allow_duplicates=False)
    raw_angle = input("Clockwise angle (90, 180, or 270): ").strip()
    if raw_angle not in {"90", "180", "270"}:
        raise ValueError("Angle must be 90, 180, or 270 degrees.")
    destination = choose_output(source, "rotated")
    overwrite = destination.exists() and confirm(f"Overwrite {destination}?")
    if destination.exists() and not overwrite:
        print("Cancelled.")
        return
    rotate_pages(source, selected, int(raw_angle), destination, overwrite=overwrite)
    print(f"Created {destination}")


def run_extract() -> None:
    source = choose_input()
    page_count = get_page_count(source)
    print(f"Document has {page_count} page(s).")
    print("Order is preserved, so '5,1-3' can also reorder pages.")
    selected = parse_pages(input("Pages to extract: "), page_count)
    destination = choose_output(source, "extracted")
    overwrite = destination.exists() and confirm(f"Overwrite {destination}?")
    if destination.exists() and not overwrite:
        print("Cancelled.")
        return
    extract_pages(source, selected, destination, overwrite=overwrite)
    print(f"Created {destination}")


def run_info() -> None:
    source = choose_input()
    size = source.stat().st_size
    with open_pdf(source) as reader:
        print(f"\nFile:      {source}")
        print(f"Size:      {size / 1024:.1f} KiB")
        print(f"Pages:     {len(reader.pages)}")
        print(f"Encrypted: {'yes' if reader.is_encrypted else 'no'}")
        if reader.pages:
            box = reader.pages[0].mediabox
            width, height = float(box.width), float(box.height)
            print(f"Page 1:    {width:.1f} x {height:.1f} points")
        metadata = safe_metadata(reader)
        for label, key in (("Title", "/Title"), ("Author", "/Author"),
                           ("Subject", "/Subject"), ("Creator", "/Creator")):
            if metadata.get(key):
                print(f"{label + ':':<11}{metadata[key]}")


MENU = {
    "1": ("Merge PDF files", run_merge),
    "2": ("Split a PDF", run_split),
    "3": ("Remove pages", run_remove),
    "4": ("Rotate pages", run_rotate),
    "5": ("Extract or reorder pages", run_extract),
    "6": ("Show PDF information", run_info),
}


def main() -> int:
    print(f"\n{APP_NAME}\n{'=' * len(APP_NAME)}")
    while True:
        print()
        for key, (label, _) in MENU.items():
            print(f"  {key}. {label}")
        print("  0. Exit")
        choice = input("\nChoose an action: ").strip()
        if choice in {"0", "q", "quit", "exit"}:
            print("Goodbye.")
            return 0
        item = MENU.get(choice)
        if not item:
            print("Please choose a number from the menu.")
            continue
        try:
            print()
            item[1]()
        except (EOFError, KeyboardInterrupt):
            print("\nCancelled.")
        except (OSError, PdfReadError, ValueError) as error:
            print(f"Error: {error}", file=sys.stderr)
        except Exception as error:  # Keep the menu alive for malformed PDFs.
            print(f"Unexpected error: {error}", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
