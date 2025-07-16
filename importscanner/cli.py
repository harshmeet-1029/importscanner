import ast
import os
import sys
import logging
import argparse
from pathlib import Path
from functools import lru_cache
import importlib.metadata
from stdlib_list import stdlib_list

# Optional Submodule to Package Mapping
SUBMODULE_TO_PACKAGE = {
    "pkg_resources": "setuptools",
    # Extend this as needed
}


# ──────────────────────────────
# Logger Setup
# ──────────────────────────────
def setup_logger(enable_file_log=False):
    logger = logging.getLogger("importscanner")
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()

    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_formatter = logging.Formatter("[%(levelname)s] %(message)s")
    console_handler.setFormatter(console_formatter)
    logger.addHandler(console_handler)

    if enable_file_log:
        file_handler = logging.FileHandler("importscanner.log")
        file_handler.setLevel(logging.DEBUG)
        file_formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
        file_handler.setFormatter(file_formatter)
        logger.addHandler(file_handler)

    return logger


# ──────────────────────────────
# Caching & Discovery
# ──────────────────────────────
@lru_cache()
def get_all_installed_top_level_modules() -> set:
    top_level_modules = set()
    for dist in importlib.metadata.distributions():
        try:
            top_level_txt = dist.read_text("top_level.txt")
            if top_level_txt:
                top_level_modules.update(
                    line.strip() for line in top_level_txt.splitlines() if line.strip()
                )
            else:
                files = dist.files or []
                for file in files:
                    if str(file).endswith("__init__.py"):
                        top_level = str(file).split("/")[0].split("\\")[0]
                        top_level_modules.add(top_level)
        except Exception:
            continue
    return top_level_modules


# ──────────────────────────────
# Detection Logic
# ──────────────────────────────
def is_stdlib(module_name: str) -> bool:
    try:
        version_str = f"{sys.version_info.major}.{sys.version_info.minor}"
        return module_name in stdlib_list(version_str)
    except Exception as e:
        logger.warning(f"Failed to check if '{module_name}' is stdlib: {e}")
        return False


@lru_cache()
def is_installed_package(module_name: str) -> bool:
    try:
        mod = module_name.lower()
        installed = {m.lower() for m in get_all_installed_top_level_modules()}
        if mod in installed:
            return True

        actual_name = SUBMODULE_TO_PACKAGE.get(mod, mod)
        importlib.metadata.version(actual_name)
        return True

    except importlib.metadata.PackageNotFoundError:
        return False
    except Exception as e:
        logger.warning(f"Error checking installed package for '{module_name}': {e}")
        return False


# ──────────────────────────────
# File Parsing
# ──────────────────────────────
def extract_imports_from_file(file_path: str) -> set:
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=file_path)
    except (SyntaxError, UnicodeDecodeError, OSError) as e:
        logger.error(f"Skipping {file_path}: {e}")
        return set()

    imports = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.add(node.module.split(".")[0])
    return imports


def is_local_module(module_name: str) -> bool:
    return not is_stdlib(module_name) and not is_installed_package(module_name)


def scan_directory(path: str) -> set:
    all_imports = set()
    logger.info(f"📂 Scanning directory: {path}")
    for root, _, files in os.walk(path):
        for file in files:
            if file.endswith(".py"):
                file_path = os.path.join(root, file)
                imports = extract_imports_from_file(file_path)
                all_imports |= imports
    logger.debug(f"🔍 Total unique imports found: {len(all_imports)}")
    return all_imports


def classify_imports(imports: set):
    stdlib = set()
    third_party = set()
    local = set()

    for module in imports:
        if is_stdlib(module):
            stdlib.add(module)
        elif is_installed_package(module):
            third_party.add(module)
        elif is_local_module(module):
            local.add(module)
        else:
            third_party.add(module)

    return stdlib, third_party, local


def save_requirements(third_party: set):
    logger.info("💾 Saving requirements.txt...")
    try:
        with open("requirements.txt", "w") as f:
            for pkg in sorted(third_party):
                try:
                    version = importlib.metadata.version(pkg)
                    f.write(f"{pkg}=={version}\n")
                except Exception as e:
                    logger.warning(f"⚠️ Could not get version for '{pkg}': {e}")
                    f.write(f"{pkg}\n")
        logger.info("✅ requirements.txt saved.")
    except Exception as e:
        logger.error(f"❌ Failed to save requirements.txt: {e}")


# ──────────────────────────────
# CLI Entry Point
# ──────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        prog="list-imports",
        description="🔍 List and classify Python imports (standard, third-party, local) in your project directory.",
        epilog="""
Examples:
  list-imports
      Scan current directory and show all imports grouped by type.

  list-imports ./src --save
      Scan './src' directory and save third-party packages to requirements.txt

  list-imports --log
      Enable logging to 'importscanner.log'
""",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument(
        "path", nargs="?", default=".", help="Directory to scan (default: current)"
    )
    parser.add_argument(
        "--save",
        action="store_true",
        help="Save third-party packages to requirements.txt",
    )
    parser.add_argument(
        "--log", action="store_true", help="Enable logging to importscanner.log"
    )

    args = parser.parse_args()

    global logger
    logger = setup_logger(enable_file_log=args.log)

    try:
        path = Path(args.path).resolve()
        if not path.exists() or not path.is_dir():
            logger.error(f"❌ Invalid path: {path}")
            print(f"❌ Invalid path: {path}")
            return

        all_imports = scan_directory(str(path))
        stdlib, third_party, local = classify_imports(all_imports)

        print("\n📦 Third-Party Packages (installed via pip):")
        print(
            "  - " + "\n  - ".join(sorted(third_party))
            if third_party
            else "  (None detected)"
        )

        print("\n📁 Local Modules (your own project's files/modules):")
        print("  - " + "\n  - ".join(sorted(local)) if local else "  (None detected)")

        print("\n📚 Standard Library (built-in Python modules):")
        print("  - " + "\n  - ".join(sorted(stdlib)) if stdlib else "  (None detected)")

        if args.save:
            save_requirements(third_party)

    except Exception as e:
        logger.exception(f"❌ Unexpected error: {e}")
        print("❌ An unexpected error occurred. Check importscanner.log for details.")


if __name__ == "__main__":
    main()
