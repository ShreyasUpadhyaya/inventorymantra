import { redirect } from "next/navigation";

import { HOME } from "@/lib/roles";

// src/proxy.ts normally redirects "/" before this renders; this is the fallback.
export default function Home() {
  redirect(HOME);
}
