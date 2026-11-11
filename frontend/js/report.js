/**
 * "Download report (.docx)": the Word report of the current search (backend services/report_docx.py), with the
 * network drawn as Figure 1. Shared by the gene / drug results (main.js) and the VCF results (vcf.js).
 */
import { api } from "./api.js";

export async function downloadReport(query, network, button, status) {
  if (!query?.id) return;
  const label = button.textContent;
  button.disabled = true;
  button.textContent = "Preparing report…";
  status.textContent = "";
  try {
    let figure = null;
    try {
      figure = network.snapshot(query.evidence?.network);
    } catch (err) {
      console.warn("Network figure skipped:", err); // the report is still useful without it
    }
    const { blob, filename } = await api.reportDocx(query.id, figure || {});
    const url = URL.createObjectURL(blob);
    const link = Object.assign(document.createElement("a"), { href: url, download: filename });
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 10_000);
    status.textContent = `Saved ${filename}`;
  } catch (err) {
    status.textContent = `Report failed: ${err.message}`;
  } finally {
    button.disabled = false;
    button.textContent = label;
  }
}
