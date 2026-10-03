import { TrainFront } from "lucide-react";
import type { Metadata } from "next";
import { Suspense } from "react";

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";

import { LoginForm } from "./login-form";

export const metadata: Metadata = { title: "Sign in · InventoryMantra" };

export default function LoginPage() {
  return (
    <main className="flex flex-1 items-center justify-center bg-muted/40 px-4 py-16">
      <Card className="w-full max-w-sm">
        <CardHeader className="text-center">
          <TrainFront className="mx-auto size-8 text-primary" aria-hidden />
          <CardTitle className="text-xl">InventoryMantra</CardTitle>
          <CardDescription>
            Stock, orders and spend, in English or हिंदी.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <Suspense>
            <LoginForm />
          </Suspense>
        </CardContent>
      </Card>
    </main>
  );
}
