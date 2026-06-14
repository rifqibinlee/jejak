    // Guard: if SheetJS didn't load, patch gensetXlsxLoaded with a clear error
    window.addEventListener('load', function() {
        if (typeof XLSX === 'undefined') {
            window.gensetXlsxLoaded = function() {
                alert('Excel library failed to load. Please check your internet connection and refresh the page.');
            };
        }
    });
