import { Card, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";

/** Placeholder for pages built in a later commit, so the shell and guards can be tried now. */
export function ComingSoon({ title, commit, text }: { title: string; commit: string; text: string }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>{title}</CardTitle>
        <CardDescription>
          {text} <span className="text-xs">(arrives in {commit})</span>
        </CardDescription>
      </CardHeader>
    </Card>
  );
}
