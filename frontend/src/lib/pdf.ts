"use client";

function encodeAscii(value: string): Uint8Array {
  return new TextEncoder().encode(value);
}

function encodePdfLiteralString(value: string): string {
  const asciiText = value
    .normalize("NFKD")
    .replace(/[\u0300-\u036f]/g, "")
    .replace(/[^\x20-\x7E]/g, "?");

  return `(${asciiText.replace(/\\/g, "\\\\").replace(/\(/g, "\\(").replace(/\)/g, "\\)")})`;
}

function wrapText(value: string, maxLineLength = 88): string[] {
  const normalized = value.trim();
  if (!normalized) {
    return [""];
  }

  const words = normalized.split(/\s+/);
  const lines: string[] = [];
  let currentLine = "";
  for (const word of words) {
    const candidate = currentLine ? `${currentLine} ${word}` : word;
    if (candidate.length <= maxLineLength) {
      currentLine = candidate;
      continue;
    }
    if (currentLine) {
      lines.push(currentLine);
    }
    currentLine = word;
  }
  if (currentLine) {
    lines.push(currentLine);
  }
  return lines;
}

export function buildDeliveryCoverPdf({
  vaultName,
  deliveredAt,
  ownerDisplayName,
  ownerMessage,
  fileNames,
}: {
  vaultName: string;
  deliveredAt?: string | null;
  ownerDisplayName?: string | null;
  ownerMessage?: string | null;
  fileNames: string[];
}): Uint8Array {
  const lines: Array<{ text: string; size: number }> = [
    { text: "Last Writes Delivery Package", size: 22 },
    { text: "", size: 12 },
    { text: `Vault: ${vaultName.trim() || "Unnamed Vault"}`, size: 12 },
    { text: deliveredAt ? `Delivered: ${deliveredAt}` : "", size: 12 },
    { text: "", size: 12 },
    { text: "Owner Message", size: 16 },
  ];

  for (const messageLine of wrapText(ownerMessage?.trim() || "No personal message was provided by the vault owner.")) {
    lines.push({ text: messageLine, size: 12 });
  }

  lines.push({ text: "", size: 12 });
  lines.push({ text: "From", size: 16 });
  lines.push({ text: ownerDisplayName?.trim() || "Unknown owner", size: 12 });
  lines.push({ text: "", size: 12 });
  lines.push({ text: "Included Files", size: 16 });
  for (const fileName of fileNames) {
    lines.push({ text: `- ${fileName}`, size: 12 });
  }

  let y = 790;
  const commands: string[] = [];
  for (const line of lines) {
    const encodedText = encodePdfLiteralString(line.text);
    commands.push(`BT /F1 ${line.size} Tf 50 ${y} Td ${encodedText} Tj ET`);
    y -= line.size >= 20 ? 30 : line.size >= 16 ? 22 : 16;
    if (y < 50) {
      break;
    }
  }

  const contentStream = commands.join("\n");
  const objects = [
    "<< /Type /Catalog /Pages 2 0 R >>",
    "<< /Type /Pages /Count 1 /Kids [3 0 R] >>",
    "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
    "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    `<< /Length ${contentStream.length} >>\nstream\n${contentStream}\nendstream`,
  ];

  let pdf = "%PDF-1.4\n";
  const offsets: number[] = [0];
  for (let index = 0; index < objects.length; index += 1) {
    offsets.push(pdf.length);
    pdf += `${index + 1} 0 obj\n${objects[index]}\nendobj\n`;
  }

  const xrefOffset = pdf.length;
  pdf += `xref\n0 ${objects.length + 1}\n`;
  pdf += "0000000000 65535 f \n";
  for (let index = 1; index < offsets.length; index += 1) {
    pdf += `${String(offsets[index]).padStart(10, "0")} 00000 n \n`;
  }
  pdf += `trailer\n<< /Size ${objects.length + 1} /Root 1 0 R >>\nstartxref\n${xrefOffset}\n%%EOF`;

  return encodeAscii(pdf);
}
