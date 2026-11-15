## Mockup website / dashboard / ERP: acceptance workflow

Apply only when the user requests a visual mockup. Deliver actual PNGs plus editable
HTML/CSS/source and opening instructions. A static mockup does not establish a working
ERP, database, API, search or navigation. Use fictional data and preserve the requested
brand, language and numbers. Do not access production financial/customer systems.

For UI typography, render the source in an isolated browser. Keep generated raster
assets for photos/illustrations where useful. Never replace a requested screenshot
with a text description or a raw image-model render of unreadable UI text.

Check each requested frame at deviceScaleFactor=1 and exact width/height. A 1440x1080
PNG is not a pass if the document extends to 1493px and required content is cropped.
DOM presence/isVisible alone is insufficient: required KPI values, chart, every table
row, activity and footer must fit inside the frame without ancestor clipping. Do not
fake a pass with global overflow:hidden, zoom, raster shrinking or hidden required data.

Also check normal responsive widths 360, 390, 768, 834, 1280 and 1440. Ordinary pages
may scroll vertically; fixed mockup contracts with fit=true must fit their chosen frame.
Use responsive grids and min-width:0; reflow dense tables to readable cards/rows when
needed. Supporting text must remain at least 12px. Report static controls honestly.
Do not split a single month/button word into fragments. Use readable localized short
month labels on mobile, preserve full month names in accessible text/data and desktop,
and inspect actual pixels after every typography change. A VLM can miss ugly wrapping.

Use the existing qa_matrix tool. Its optional frames entries have name, width, height,
fit and requiredSelectors; omit frames for the standard six-profile matrix. Use a new,
empty output directory per run. ok=true means the audit executed; verdict=needs-work
must not be reported as a design pass. Browser errors, blocked/failed requests, clipping,
missing required regions and output pixel sizes affect the verdict. Review real PNGs
and domain consistency separately (for example monthly revenue versus chart values).

On Linux the implementation is /opt/harmonika-ai/visual-quality/qa_matrix.cjs and reuses
the existing isolated browser runtime. On a client, use its advertised tool/runtime;
never assume a Mac source path exists on Linux. Preview only the authorized project on
a free loopback port, never port 3000. The existing network/output firewall remains on.
The Linux operator connector uses Chromium screen-media PDF export plus CPU rasterization
when compositor capture is unavailable. Preserve captureMethod=chromium-screen-pdf-raster;
do not call it a compositor screenshot. Multi-page output must fail rather than drop pages.
Inspect the actual raster; print fragmentation and dynamic media can differ from a screen.
Re-read actual formatted source before targeted edits, fix findings and repeat the same
checks. No automatic model training or customer-history collection is implied.
