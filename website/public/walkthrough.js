Reveal.initialize({
  width: 1200,
  height: 900,
  margin: 0.08,
  hash: true,
  slideNumber: "c/t",
  transition: "none",
  pdfSeparateFragments: false,
});
const resize = () =>
  Reveal.configure({
    width: innerWidth <= 600 ? innerWidth : 1200,
    height: innerWidth <= 600 ? innerHeight : 900,
  });
addEventListener("resize", resize);
resize();
for (const diagram of document.querySelectorAll(".diagram-scroll")) {
  diagram.addEventListener("keydown", (event) => {
    if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
      event.stopPropagation();
    }
  });
}
