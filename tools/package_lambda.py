"""Package an installed Linux dependency directory and public Python source only."""
import argparse
import hashlib
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED

parser = argparse.ArgumentParser()
parser.add_argument("--dependencies", required=True)
parser.add_argument("--destination", default="dist/invoice-lambda.zip")
args = parser.parse_args()
destination = Path(args.destination)
destination.parent.mkdir(parents=True, exist_ok=True)
with ZipFile(destination, "w", ZIP_DEFLATED) as archive:
    for root in (Path(args.dependencies), Path("src")):
        for path in root.rglob("*"):
            if path.is_file() and "__pycache__" not in path.parts and path.suffix not in (".pyc", ".pyo"):
                archive.write(path, path.relative_to(root))
print("Archive created:", destination, "SHA256:", hashlib.sha256(destination.read_bytes()).hexdigest())
