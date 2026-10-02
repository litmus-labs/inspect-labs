// Uses Mozilla PDF.js's public viewer components. The Apache license is included in the build.
import * as pdfjsLib from "/vendor/pdfjs/pdf.mjs";
const { EventBus, PDFLinkService, PDFViewer } = await import(
  "/vendor/pdfjs/pdf_viewer.mjs"
);
pdfjsLib.GlobalWorkerOptions.workerSrc = "/vendor/pdfjs/pdf.worker.mjs";
const container = document.getElementById("pdf-container");
const eventBus = new EventBus();
const linkService = new PDFLinkService({ eventBus });
const viewer = new PDFViewer({
  container,
  eventBus,
  linkService,
  imageResourcesPath: "/vendor/pdfjs/images/",
});
linkService.setViewer(viewer);
const previous = document.getElementById("previous-page");
const next = document.getElementById("next-page");
const zoom = document.getElementById("paper-zoom");
const position = document.getElementById("page-position");
const status = document.getElementById("paper-status");
function updatePosition() {
  position.textContent = `Page ${viewer.currentPageNumber} of ${viewer.pagesCount}`;
  previous.disabled = viewer.currentPageNumber <= 1;
  next.disabled = viewer.currentPageNumber >= viewer.pagesCount;
}
eventBus.on("pagesinit", () => {
  viewer.currentScaleValue = "page-width";
  zoom.disabled = false;
  updatePosition();
});
eventBus.on("pagechanging", updatePosition);
previous.addEventListener("click", () => {
  viewer.currentPageNumber -= 1;
});
next.addEventListener("click", () => {
  viewer.currentPageNumber += 1;
});
zoom.addEventListener("change", () => {
  viewer.currentScaleValue = zoom.value;
});
new ResizeObserver(() => {
  if (viewer.pagesCount && zoom.value === "page-width")
    viewer.currentScaleValue = "page-width";
}).observe(container);
try {
  const pdfDocument = await pdfjsLib.getDocument({
    url: "/assets/inspect-labs-paper.pdf",
    cMapUrl: "/vendor/pdfjs/cmaps/",
    cMapPacked: true,
    standardFontDataUrl: "/vendor/pdfjs/standard_fonts/",
    wasmUrl: "/vendor/pdfjs/wasm/",
  }).promise;
  viewer.setDocument(pdfDocument);
  linkService.setDocument(pdfDocument);
  status.textContent =
    "Scroll to read. Text and references can be selected in the embedded reader.";
} catch {
  position.textContent = "Paper unavailable";
  status.textContent =
    "The embedded reader could not load. Use the open or download PDF links above.";
}
