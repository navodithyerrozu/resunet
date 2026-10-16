import { Card } from "@/components/ui/primitives";

export default function ProfileLoading() {
  return (
    <div className="space-y-6" aria-busy="true" aria-label="Loading career profile">
      <div className="h-16 animate-pulse rounded-card bg-sunken" />
      <Card title="Loading contact and positioning" eyebrow="Profile">
        <div className="grid gap-4 p-5 md:grid-cols-2">
          {Array.from({ length: 8 }, (_, index) => (
            <div key={index} className="h-16 animate-pulse rounded-card bg-sunken" />
          ))}
        </div>
      </Card>
      <Card title="Loading experience" eyebrow="Work history">
        <div className="m-5 h-48 animate-pulse rounded-card bg-sunken" />
      </Card>
    </div>
  );
}
