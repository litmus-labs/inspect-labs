import { cp, mkdir, readFile, readdir, rm, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import path from "node:path";

const root = path.dirname(fileURLToPath(import.meta.url));
const output = path.join(root, "dist");
const publishing = process.argv.includes("--publish");
const config = {
  DOCS_URL: (process.env.DOCS_URL || "http://localhost:3333").replace(
    /\/+$/,
    "",
  ),
  SOURCE_URL: process.env.SOURCE_URL || "/source/",
  SITE_URL: (process.env.SITE_URL || "http://localhost:4321").replace(
    /\/+$/,
    "",
  ),
};
if (publishing) {
  for (const name of Object.keys(config)) {
    const value = process.env[name];
    let url;
    try {
      url = new URL(value);
    } catch {
      /* Invalid values fail the gate below. */
    }
    if (
      !url ||
      url.protocol !== "https:" ||
      !url.hostname ||
      url.username ||
      url.password ||
      /^(localhost|\[::1\]|127\.)/.test(url.hostname) ||
      url.hostname.endsWith(".localhost")
    ) {
      throw new Error(
        `${name} must be an approved public HTTPS URL for publication.`,
      );
    }
  }
}
await rm(output, { recursive: true, force: true });
await mkdir(output, { recursive: true });
await cp(path.join(root, "public"), output, { recursive: true });
const pdfSource = path.join(root, "node_modules/pdfjs-dist");
const pdfOutput = path.join(output, "vendor/pdfjs");
await mkdir(pdfOutput, { recursive: true });
for (const [source, destination] of [
  ["build/pdf.mjs", "pdf.mjs"],
  ["build/pdf.worker.mjs", "pdf.worker.mjs"],
  ["web/pdf_viewer.mjs", "pdf_viewer.mjs"],
  ["web/pdf_viewer.css", "pdf_viewer.css"],
  ["web/images", "images"],
  ["cmaps", "cmaps"],
  ["standard_fonts", "standard_fonts"],
  ["wasm", "wasm"],
  ["LICENSE", "LICENSE"],
]) {
  await cp(path.join(pdfSource, source), path.join(pdfOutput, destination), {
    recursive: true,
  });
}
const header = await readFile(path.join(root, "src/header.html"), "utf8");
const footer = await readFile(path.join(root, "src/footer.html"), "utf8");
for (const name of publishing
  ? ["index", "paper", "walkthrough"]
  : ["index", "paper", "source", "walkthrough"]) {
  let html = await readFile(path.join(root, `src/${name}.html`), "utf8");
  html = html.replace("{{HEADER}}", header).replace("{{FOOTER}}", footer);
  const canonical = `${config.SITE_URL}/${name === "index" ? "" : `${name}/`}`;
  html = html.replace(
    "</head>",
    `<link rel="canonical" href="${canonical.replaceAll("&", "&amp;").replaceAll('"', "&quot;")}"></head>`,
  );
  for (const [key, value] of Object.entries(config)) {
    html = html.replaceAll(
      `{{${key}}}`,
      value.replaceAll("&", "&amp;").replaceAll('"', "&quot;"),
    );
  }
  if (/\{\{\w+\}\}/.test(html))
    throw new Error(`Unresolved template in ${name}`);
  if (!publishing)
    html = html.replace(
      "</head>",
      '<meta name="robots" content="noindex"></head>',
    );
  const directory = name === "index" ? output : path.join(output, name);
  await mkdir(directory, { recursive: true });
  await writeFile(path.join(directory, "index.html"), html);
}
// Fail on broken local references before the site is served.
async function visit(directory) {
  for (const item of await readdir(directory, { withFileTypes: true })) {
    const filename = path.join(directory, item.name);
    if (item.isDirectory()) {
      await visit(filename);
      continue;
    }
    if (!filename.endsWith(".html")) continue;
    const html = await readFile(filename, "utf8");
    for (const match of html.matchAll(/(?:href|src)="(\/[^"#?]*)/g)) {
      const target = path.join(output, match[1]);
      await readFile(target.endsWith("/") ? `${target}index.html` : target);
    }
  }
}
await visit(output);
console.log(
  `Built ${publishing ? "publication candidate" : "local preview"} in website/dist`,
);
