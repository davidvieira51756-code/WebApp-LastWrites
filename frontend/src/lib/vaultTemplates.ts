export type VaultTemplateId =
  | "blank"
  | "family"
  | "crypto"
  | "freelancer"
  | "letters";

export type VaultTemplateDefinition = {
  id: VaultTemplateId;
  name: string;
  shortDescription: string;
  vaultNamePlaceholder: string;
  ownerMessagePlaceholder: string;
  ownerMessageGuidance: string;
  sections: string[];
  fileChecklist: string[];
  starterOutline: string;
};

export const VAULT_TEMPLATES: VaultTemplateDefinition[] = [
  {
    id: "blank",
    name: "Blank Vault",
    shortDescription: "Start from scratch and decide the structure yourself.",
    vaultNamePlaceholder: "Family Legacy Vault",
    ownerMessagePlaceholder: "Write the message that should appear on the delivery cover page.",
    ownerMessageGuidance:
      "Use this space to explain what recipients should read first, any priorities, and how to approach the rest of the vault.",
    sections: [
      "First instructions and priorities",
      "Important people to contact",
      "Accounts, subscriptions, or services",
      "Sensitive documents or files to review",
    ],
    fileChecklist: [
      "IDs, insurance, or legal documents",
      "Account summaries or references",
      "Notes about where critical files are stored",
    ],
    starterOutline: [
      "[First instructions]",
      "- What should be handled first?",
      "- Are there any urgent deadlines or practical steps?",
      "",
      "[Important contacts]",
      "- Who should be contacted first and why?",
      "",
      "[Accounts and services]",
      "- Which accounts, providers, or services matter most?",
      "",
      "[Documents and files]",
      "- Which files in this vault should be read first?",
    ].join("\n"),
  },
  {
    id: "family",
    name: "Family",
    shortDescription: "General guidance, contacts, wishes, and practical documents for family.",
    vaultNamePlaceholder: "Family Instructions Vault",
    ownerMessagePlaceholder: "Explain what your family should know first and how to use the contents of this vault.",
    ownerMessageGuidance:
      "Focus on what your family should do first, who to contact, which documents matter most, and any personal wishes you want them to keep in mind.",
    sections: [
      "General instructions for the first days",
      "Important family, legal, and medical contacts",
      "Where key documents are and what they are for",
      "Bills, subscriptions, and household services",
      "Personal or funeral wishes",
    ],
    fileChecklist: [
      "Will, insurance, and identification documents",
      "Contact lists and emergency references",
      "Household, banking, or service records",
    ],
    starterOutline: [
      "[First steps for the family]",
      "- What should be done in the first days?",
      "- Are there urgent calls, appointments, or decisions?",
      "",
      "[Important contacts]",
      "- Family, lawyer, doctor, accountant, employer, or other key contacts.",
      "",
      "[Key documents]",
      "- Which documents are attached here and what each one is for.",
      "",
      "[Accounts and services]",
      "- Which bills, subscriptions, or household matters need attention.",
      "",
      "[Personal wishes]",
      "- Any personal, ceremonial, or practical wishes you want respected.",
    ].join("\n"),
  },
  {
    id: "crypto",
    name: "Crypto / Digital Assets",
    shortDescription: "Explain where assets are, how access works, and who can help safely.",
    vaultNamePlaceholder: "Digital Assets Recovery Vault",
    ownerMessagePlaceholder: "Explain where the assets are and what precautions recipients should take before trying to access them.",
    ownerMessageGuidance:
      "Describe where the assets exist, how access and recovery work at a high level, what not to do, and which trusted person or professional can help.",
    sections: [
      "What assets exist and where they are held",
      "How access works at a high level",
      "Recovery phrases, devices, or custody notes",
      "Security warnings and mistakes to avoid",
      "Trusted technical, legal, or tax contacts",
    ],
    fileChecklist: [
      "Wallet inventories or exchange summaries",
      "Recovery instructions and device references",
      "Contacts for lawyers, accountants, or trusted experts",
    ],
    starterOutline: [
      "[Asset overview]",
      "- What digital assets exist?",
      "- Where are they stored: exchange, hardware wallet, software wallet, multisig, etc.?",
      "",
      "[Access model]",
      "- What is needed to access them at a high level?",
      "- Which files in this vault explain the process?",
      "",
      "[Security warnings]",
      "- What should recipients avoid doing before speaking to a trusted person?",
      "- Are there scams, lockout risks, or device handling concerns?",
      "",
      "[Trusted contacts]",
      "- Who can help with technical, legal, or tax questions?",
    ].join("\n"),
  },
  {
    id: "freelancer",
    name: "Freelancer / Business",
    shortDescription: "Client continuity, contracts, invoices, tools, and operational access.",
    vaultNamePlaceholder: "Business Continuity Vault",
    ownerMessagePlaceholder: "Explain what should happen first in the business and which clients, tools, or obligations need attention.",
    ownerMessageGuidance:
      "Summarize which clients or deadlines matter first, how to access the critical tools, and whether the goal is to pause, transfer, or close ongoing work.",
    sections: [
      "Business priorities in the first week",
      "Active clients, projects, and deadlines",
      "Contracts, invoices, and billing records",
      "Tools, domains, hosting, and service accounts",
      "Trusted collaborators, accountant, or lawyer",
    ],
    fileChecklist: [
      "Client lists and contract documents",
      "Invoices, bookkeeping, and tax references",
      "Service inventories for domains, hosting, email, and tooling",
    ],
    starterOutline: [
      "[Immediate business priorities]",
      "- What needs attention first?",
      "- Should current work be paused, completed, transferred, or closed?",
      "",
      "[Clients and deadlines]",
      "- Which clients or projects are active?",
      "- Are there urgent deliverables or renewal dates?",
      "",
      "[Money and contracts]",
      "- Which attached files cover invoices, contracts, subscriptions, or taxes?",
      "",
      "[Tools and services]",
      "- Which systems are critical: domains, hosting, email, storage, payment providers, etc.?",
      "",
      "[Trusted contacts]",
      "- Who can help manage business continuity?",
    ].join("\n"),
  },
  {
    id: "letters",
    name: "Personal Letters",
    shortDescription: "A lighter vault centered on personal messages and context for recipients.",
    vaultNamePlaceholder: "Letters For Loved Ones",
    ownerMessagePlaceholder: "Use this space to explain the intent of the letters or how recipients should approach them.",
    ownerMessageGuidance:
      "Keep this more personal than procedural. You can explain why you prepared the letters, whether they should be read privately or shared, and any context that matters.",
    sections: [
      "Context for the letters",
      "Who each letter is for",
      "When or how the letters should be read",
      "Any attachments that support the messages",
    ],
    fileChecklist: [
      "Letter files grouped by recipient",
      "Photos, scans, or memory documents",
      "Optional notes about timing or privacy",
    ],
    starterOutline: [
      "[Context]",
      "- Why did you prepare these letters?",
      "- Is there anything recipients should know before reading them?",
      "",
      "[How to use this vault]",
      "- Which files are for which people?",
      "- Are any messages private or intended to be shared?",
      "",
      "[Additional notes]",
      "- Any extra context, memories, or guidance that supports the letters.",
    ].join("\n"),
  },
];

export function getVaultTemplateById(templateId: VaultTemplateId): VaultTemplateDefinition {
  return (
    VAULT_TEMPLATES.find((template) => template.id === templateId) ??
    VAULT_TEMPLATES[0]
  );
}
