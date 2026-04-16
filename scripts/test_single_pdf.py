import sys
sys.path.append('..')

from src.pdf_processor import PDFProcessor

# Test with the device we found earlier
test_k_number = "K101273"

processor = PDFProcessor()
result = processor.extract_with_metadata(test_k_number)

if result['success']:
    print(f"✓ Successfully processed {test_k_number}")
    print(f"Extracted {result['char_count']} characters")
    print("\nFirst 500 characters:")
    print(result['text'][:500])
else:
    print(f"✗ Failed to process {test_k_number}")
