from build_canonical_20_09_package import (
    COMPAT_ZIP_PATHS,
    EXCLUDED_DIR_NAMES,
    EXCLUDED_FILE_NAMES,
    ZIP_OUTPUT_NAME,
)


def test_package_excludes_git_history_and_external_hash_receipts():
    assert ".git" in EXCLUDED_DIR_NAMES
    assert {
        "zip-checksum-verification.json",
        "zip_sha256_receipt.json",
    }.issubset(EXCLUDED_FILE_NAMES)


def test_package_preserves_historical_archives():
    assert ZIP_OUTPUT_NAME == "2009.zip"
    assert "1909.zip" not in {path.name for path in COMPAT_ZIP_PATHS}
