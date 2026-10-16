"use client";

import { useState } from "react";

import { buttonClass, Card } from "@/components/ui/primitives";

import type {
  CareerProfile,
  Certification,
  EducationEntry,
  EmploymentType,
  ExperienceEntry,
  ProjectEntry,
  Skill,
  SkillProficiency,
} from "@resunet/shared";

type Props = { initialProfile: CareerProfile };
type TextFieldProps = {
  label: string;
  value: string;
  onChange: (value: string) => void;
  type?: "text" | "email" | "url" | "date" | "number";
  required?: boolean;
  placeholder?: string;
};

const inputClass =
  "mt-1 w-full rounded-card border border-line bg-paper px-3 py-2 text-sm text-ink outline-none focus:border-accent";
const selectClass = inputClass;
const textAreaClass = `${inputClass} min-h-24 resize-y`;

function TextField({
  label,
  value,
  onChange,
  type = "text",
  required = false,
  placeholder,
}: TextFieldProps) {
  return (
    <label className="block text-xs font-500 text-muted">
      {label}
      <input
        className={inputClass}
        type={type}
        value={value}
        required={required}
        placeholder={placeholder}
        onChange={(event) => onChange(event.target.value)}
      />
    </label>
  );
}

function TextArea({
  label,
  value,
  onChange,
  rows = 3,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  rows?: number;
}) {
  return (
    <label className="block text-xs font-500 text-muted">
      {label}
      <textarea
        className={textAreaClass}
        rows={rows}
        value={value}
        onChange={(event) => onChange(event.target.value)}
      />
    </label>
  );
}

function newId(): string {
  return crypto.randomUUID().replaceAll("-", "").slice(0, 24);
}

function textValue(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function commaList(value: string): string[] {
  return value
    .split(",")
    .map((part) => part.trim())
    .filter(Boolean);
}

function emptyExperience(): ExperienceEntry {
  return {
    entry_id: newId(),
    company: "",
    title: "",
    location: null,
    employment_type: "full_time",
    dates: { start: "", end: null },
    tech_stack: [],
    raw_notes: "",
  };
}

function emptyProject(): ProjectEntry {
  return {
    entry_id: newId(),
    name: "",
    role: null,
    dates: null,
    url: null,
    repo_url: null,
    tech_stack: [],
    raw_notes: "",
  };
}

function emptyEducation(): EducationEntry {
  return {
    entry_id: newId(),
    institution: "",
    degree: null,
    field_of_study: null,
    dates: null,
    gpa: null,
    honors: [],
    coursework: [],
  };
}

function emptySkill(): Skill {
  return { name: "", category: null, proficiency: "proficient", years: null };
}

function emptyCertification(): Certification {
  return {
    entry_id: newId(),
    name: "",
    issuer: null,
    issued_on: null,
    expires_on: null,
    credential_url: null,
  };
}

function RemoveButton({ onClick }: { onClick: () => void }) {
  return (
    <button type="button" className={buttonClass("secondary")} onClick={onClick}>
      Remove
    </button>
  );
}

export function ProfileEditor({ initialProfile }: Props) {
  const [profile, setProfile] = useState(initialProfile);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [success, setSuccess] = useState(false);

  function patchExperience(entryId: string, patch: Partial<ExperienceEntry>) {
    setProfile((current) => ({
      ...current,
      experiences: current.experiences.map((entry) =>
        entry.entry_id === entryId ? { ...entry, ...patch } : entry,
      ),
    }));
  }

  function patchProject(entryId: string, patch: Partial<ProjectEntry>) {
    setProfile((current) => ({
      ...current,
      projects: current.projects.map((entry) =>
        entry.entry_id === entryId ? { ...entry, ...patch } : entry,
      ),
    }));
  }

  function patchEducation(entryId: string, patch: Partial<EducationEntry>) {
    setProfile((current) => ({
      ...current,
      education: current.education.map((entry) =>
        entry.entry_id === entryId ? { ...entry, ...patch } : entry,
      ),
    }));
  }

  function patchCertification(entryId: string, patch: Partial<Certification>) {
    setProfile((current) => ({
      ...current,
      certifications: current.certifications.map((entry) =>
        entry.entry_id === entryId ? { ...entry, ...patch } : entry,
      ),
    }));
  }

  async function save(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (pending) return;

    setPending(true);
    setError(null);
    setFieldErrors({});
    setSuccess(false);
    try {
      const response = await fetch("/api/profile", {
        method: "PUT",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(profile),
      });
      const body: unknown = await response.json().catch(() => null);
      if (!response.ok) {
        if (
          typeof body === "object" && body !== null &&
          "fieldErrors" in body && typeof body.fieldErrors === "object" && body.fieldErrors !== null
        ) {
          setFieldErrors(body.fieldErrors as Record<string, string>);
        }
        const message =
          typeof body === "object" && body !== null && "detail" in body &&
          typeof body.detail === "string"
            ? body.detail
            : "Could not save the profile. Review the fields and try again.";
        setError(message);
        return;
      }
      setProfile(body as CareerProfile);
      setSuccess(true);
    } catch {
      setError("Could not reach the server. Your edits are still on this page; try again.");
    } finally {
      setPending(false);
    }
  }

  return (
    <form onSubmit={(event) => void save(event)} className="space-y-6">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="eyebrow">Your source of truth</p>
          <h1 className="mt-1.5 text-title">Career profile</h1>
          <p className="mt-2 max-w-2xl text-sm text-muted">
            Keep your experience and notes here. Resume drafts can draw from these details.
          </p>
        </div>
        <div className="flex items-center gap-3">
          <span className="text-xs text-muted" aria-live="polite">
            Profile {Math.round(profile.completeness * 100)}% complete
          </span>
          <button type="submit" disabled={pending} className={buttonClass("primary")}>
            {pending ? "Saving…" : "Save profile"}
          </button>
        </div>
      </header>

      {error ? <p role="alert" className="text-sm text-danger">{error}</p> : null}
      {Object.keys(fieldErrors).length > 0 ? (
        <ul className="list-disc space-y-1 pl-5 text-sm text-danger" aria-label="Profile field errors">
          {Object.entries(fieldErrors).map(([field, message]) => (
            <li key={field}><span className="font-500">{field.replaceAll("_", " ")}: </span>{message}</li>
          ))}
        </ul>
      ) : null}
      {success ? (
        <p role="status" className="text-sm text-ok">Profile saved. Your changes are stored.</p>
      ) : null}

      <Card title="Contact and positioning" eyebrow="Profile">
        <div className="grid gap-4 p-5 md:grid-cols-2">
          <TextField
            label="Full name"
            value={profile.contact.full_name}
            required
            onChange={(value) =>
              setProfile((current) => ({
                ...current,
                contact: { ...current.contact, full_name: value },
              }))
            }
          />
          <TextField
            label="Email"
            type="email"
            value={profile.contact.email}
            required
            onChange={(value) =>
              setProfile((current) => ({
                ...current,
                contact: { ...current.contact, email: value },
              }))
            }
          />
          <TextField label="Phone" value={profile.contact.phone ?? ""} onChange={(phone) => setProfile((p) => ({ ...p, contact: { ...p.contact, phone: phone || null } }))} />
          <TextField label="Location" value={profile.contact.location ?? ""} onChange={(location) => setProfile((p) => ({ ...p, contact: { ...p.contact, location: location || null } }))} />
          <TextField label="LinkedIn URL" type="url" value={textValue(profile.contact.linkedin_url)} onChange={(linkedin_url) => setProfile((p) => ({ ...p, contact: { ...p.contact, linkedin_url: linkedin_url || null } }))} />
          <TextField label="GitHub URL" type="url" value={textValue(profile.contact.github_url)} onChange={(github_url) => setProfile((p) => ({ ...p, contact: { ...p.contact, github_url: github_url || null } }))} />
          <TextField label="Portfolio URL" type="url" value={textValue(profile.contact.portfolio_url)} onChange={(portfolio_url) => setProfile((p) => ({ ...p, contact: { ...p.contact, portfolio_url: portfolio_url || null } }))} />
          <TextField label="Headline" value={profile.headline ?? ""} onChange={(headline) => setProfile((p) => ({ ...p, headline: headline || null }))} />
          <div className="md:col-span-2">
            <TextArea label="Summary" value={profile.summary ?? ""} onChange={(summary) => setProfile((p) => ({ ...p, summary: summary || null }))} />
          </div>
        </div>
      </Card>

      <Card title="Experience" eyebrow="Work history" aside={`${profile.experiences.length} entries`}>
        <div className="space-y-5 p-5">
          {profile.experiences.map((entry) => (
            <fieldset key={entry.entry_id} className="grid gap-4 rounded-card border border-line p-4 md:grid-cols-2">
              <legend className="px-1 text-xs font-600 text-muted">Role</legend>
              <TextField label="Company" value={entry.company} required onChange={(company) => patchExperience(entry.entry_id, { company })} />
              <TextField label="Title" value={entry.title} required onChange={(title) => patchExperience(entry.entry_id, { title })} />
              <TextField label="Location" value={entry.location ?? ""} onChange={(location) => patchExperience(entry.entry_id, { location: location || null })} />
              <label className="block text-xs font-500 text-muted">Employment type
                <select className={selectClass} value={entry.employment_type} onChange={(event) => patchExperience(entry.entry_id, { employment_type: event.target.value as EmploymentType })}>
                  {(["full_time", "part_time", "contract", "internship", "freelance", "volunteer"] as EmploymentType[]).map((kind) => <option key={kind} value={kind}>{kind.replaceAll("_", " ")}</option>)}
                </select>
              </label>
              <TextField label="Start date" type="date" value={entry.dates.start} required onChange={(start) => patchExperience(entry.entry_id, { dates: { ...entry.dates, start } })} />
              <TextField label="End date (blank if current)" type="date" value={entry.dates.end ?? ""} onChange={(end) => patchExperience(entry.entry_id, { dates: { ...entry.dates, end: end || null } })} />
              <TextField label="Technology and tools (comma separated)" value={entry.tech_stack.join(", ")} onChange={(value) => patchExperience(entry.entry_id, { tech_stack: commaList(value) })} />
              <div className="md:col-span-2"><TextArea label="Raw notes and accomplishments" rows={4} value={entry.raw_notes} onChange={(raw_notes) => patchExperience(entry.entry_id, { raw_notes })} /></div>
              <div className="md:col-span-2"><RemoveButton onClick={() => setProfile((p) => ({ ...p, experiences: p.experiences.filter((item) => item.entry_id !== entry.entry_id) }))} /></div>
            </fieldset>
          ))}
          <button type="button" className={buttonClass("secondary")} onClick={() => setProfile((p) => ({ ...p, experiences: [...p.experiences, emptyExperience()] }))}>Add experience</button>
        </div>
      </Card>

      <Card title="Projects" eyebrow="Selected work" aside={`${profile.projects.length} entries`}>
        <div className="space-y-5 p-5">
          {profile.projects.map((entry) => (
            <fieldset key={entry.entry_id} className="grid gap-4 rounded-card border border-line p-4 md:grid-cols-2">
              <legend className="px-1 text-xs font-600 text-muted">Project</legend>
              <TextField label="Name" value={entry.name} required onChange={(name) => patchProject(entry.entry_id, { name })} />
              <TextField label="Role" value={entry.role ?? ""} onChange={(role) => patchProject(entry.entry_id, { role: role || null })} />
              <TextField label="Project URL" type="url" value={textValue(entry.url)} onChange={(url) => patchProject(entry.entry_id, { url: url || null })} />
              <TextField label="Repository URL" type="url" value={textValue(entry.repo_url)} onChange={(repo_url) => patchProject(entry.entry_id, { repo_url: repo_url || null })} />
              {entry.dates ? (
                <>
                  <TextField label="Start date" type="date" value={entry.dates.start} onChange={(start) => patchProject(entry.entry_id, { dates: { ...entry.dates!, start } })} />
                  <TextField label="End date" type="date" value={entry.dates.end ?? ""} onChange={(end) => patchProject(entry.entry_id, { dates: { ...entry.dates!, end: end || null } })} />
                </>
              ) : (
                <button type="button" className={buttonClass("secondary")} onClick={() => patchProject(entry.entry_id, { dates: { start: "", end: null } })}>Add dates</button>
              )}
              <TextField label="Technology (comma separated)" value={entry.tech_stack.join(", ")} onChange={(value) => patchProject(entry.entry_id, { tech_stack: commaList(value) })} />
              <div className="md:col-span-2"><TextArea label="Notes" value={entry.raw_notes} onChange={(raw_notes) => patchProject(entry.entry_id, { raw_notes })} /></div>
              {entry.dates ? <button type="button" className={buttonClass("secondary")} onClick={() => patchProject(entry.entry_id, { dates: null })}>Remove dates</button> : null}
              <div className="md:col-span-2"><RemoveButton onClick={() => setProfile((p) => ({ ...p, projects: p.projects.filter((item) => item.entry_id !== entry.entry_id) }))} /></div>
            </fieldset>
          ))}
          <button type="button" className={buttonClass("secondary")} onClick={() => setProfile((p) => ({ ...p, projects: [...p.projects, emptyProject()] }))}>Add project</button>
        </div>
      </Card>

      <Card title="Education" eyebrow="Education" aside={`${profile.education.length} entries`}>
        <div className="space-y-5 p-5">
          {profile.education.map((entry) => (
            <fieldset key={entry.entry_id} className="grid gap-4 rounded-card border border-line p-4 md:grid-cols-2">
              <legend className="px-1 text-xs font-600 text-muted">Education</legend>
              <TextField label="Institution" value={entry.institution} required onChange={(institution) => patchEducation(entry.entry_id, { institution })} />
              <TextField label="Degree" value={entry.degree ?? ""} onChange={(degree) => patchEducation(entry.entry_id, { degree: degree || null })} />
              <TextField label="Field of study" value={entry.field_of_study ?? ""} onChange={(field_of_study) => patchEducation(entry.entry_id, { field_of_study: field_of_study || null })} />
              {entry.dates ? (
                <>
                  <TextField label="Start date" type="date" value={entry.dates.start} onChange={(start) => patchEducation(entry.entry_id, { dates: { ...entry.dates!, start } })} />
                  <TextField label="End date" type="date" value={entry.dates.end ?? ""} onChange={(end) => patchEducation(entry.entry_id, { dates: { ...entry.dates!, end: end || null } })} />
                </>
              ) : (
                <button type="button" className={buttonClass("secondary")} onClick={() => patchEducation(entry.entry_id, { dates: { start: "", end: null } })}>Add dates</button>
              )}
              <TextField label="GPA (0–10)" type="number" value={entry.gpa === null ? "" : String(entry.gpa)} onChange={(value) => patchEducation(entry.entry_id, { gpa: value === "" ? null : Number(value) })} />
              <TextField label="Honors (comma separated)" value={entry.honors.join(", ")} onChange={(value) => patchEducation(entry.entry_id, { honors: commaList(value) })} />
              <TextField label="Coursework (comma separated)" value={entry.coursework.join(", ")} onChange={(value) => patchEducation(entry.entry_id, { coursework: commaList(value) })} />
              {entry.dates ? <button type="button" className={buttonClass("secondary")} onClick={() => patchEducation(entry.entry_id, { dates: null })}>Remove dates</button> : null}
              <div className="md:col-span-2"><RemoveButton onClick={() => setProfile((p) => ({ ...p, education: p.education.filter((item) => item.entry_id !== entry.entry_id) }))} /></div>
            </fieldset>
          ))}
          <button type="button" className={buttonClass("secondary")} onClick={() => setProfile((p) => ({ ...p, education: [...p.education, emptyEducation()] }))}>Add education</button>
        </div>
      </Card>

      <Card title="Skills" eyebrow="Skills" aside={`${profile.skills.length} entries`}>
        <div className="space-y-3 p-5">
          {profile.skills.map((skill, index) => (
            <div key={`${skill.name}-${index}`} className="grid items-end gap-3 rounded-card border border-line p-4 md:grid-cols-[1fr_1fr_1fr_1fr_auto]">
              <TextField label="Skill" value={skill.name} required onChange={(name) => setProfile((p) => ({ ...p, skills: p.skills.map((item, i) => i === index ? { ...item, name } : item) }))} />
              <TextField label="Category" value={skill.category ?? ""} onChange={(category) => setProfile((p) => ({ ...p, skills: p.skills.map((item, i) => i === index ? { ...item, category: category || null } : item) }))} />
              <label className="block text-xs font-500 text-muted">Proficiency
                <select className={selectClass} value={skill.proficiency} onChange={(event) => setProfile((p) => ({ ...p, skills: p.skills.map((item, i) => i === index ? { ...item, proficiency: event.target.value as SkillProficiency } : item) }))}>
                  {(["familiar", "proficient", "advanced", "expert"] as SkillProficiency[]).map((level) => <option key={level} value={level}>{level}</option>)}
                </select>
              </label>
              <TextField label="Years" type="number" value={skill.years === null ? "" : String(skill.years)} onChange={(value) => setProfile((p) => ({ ...p, skills: p.skills.map((item, i) => i === index ? { ...item, years: value === "" ? null : Number(value) } : item) }))} />
              <RemoveButton onClick={() => setProfile((p) => ({ ...p, skills: p.skills.filter((_, i) => i !== index) }))} />
            </div>
          ))}
          <button type="button" className={buttonClass("secondary")} onClick={() => setProfile((p) => ({ ...p, skills: [...p.skills, emptySkill()] }))}>Add skill</button>
        </div>
      </Card>

      <Card title="Certifications" eyebrow="Credentials" aside={`${profile.certifications.length} entries`}>
        <div className="space-y-5 p-5">
          {profile.certifications.map((entry) => (
            <fieldset key={entry.entry_id} className="grid gap-4 rounded-card border border-line p-4 md:grid-cols-2">
              <legend className="px-1 text-xs font-600 text-muted">Certification</legend>
              <TextField label="Name" value={entry.name} required onChange={(name) => patchCertification(entry.entry_id, { name })} />
              <TextField label="Issuer" value={entry.issuer ?? ""} onChange={(issuer) => patchCertification(entry.entry_id, { issuer: issuer || null })} />
              <TextField label="Issued on" type="date" value={entry.issued_on ?? ""} onChange={(issued_on) => patchCertification(entry.entry_id, { issued_on: issued_on || null })} />
              <TextField label="Expires on" type="date" value={entry.expires_on ?? ""} onChange={(expires_on) => patchCertification(entry.entry_id, { expires_on: expires_on || null })} />
              <TextField label="Credential URL" type="url" value={textValue(entry.credential_url)} onChange={(credential_url) => patchCertification(entry.entry_id, { credential_url: credential_url || null })} />
              <div><RemoveButton onClick={() => setProfile((p) => ({ ...p, certifications: p.certifications.filter((item) => item.entry_id !== entry.entry_id) }))} /></div>
            </fieldset>
          ))}
          <button type="button" className={buttonClass("secondary")} onClick={() => setProfile((p) => ({ ...p, certifications: [...p.certifications, emptyCertification()] }))}>Add certification</button>
        </div>
      </Card>

      <div className="flex justify-end">
        <button type="submit" disabled={pending} className={buttonClass("primary")}>
          {pending ? "Saving…" : "Save profile"}
        </button>
      </div>
    </form>
  );
}
