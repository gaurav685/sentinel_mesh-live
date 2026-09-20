import { redirect } from "next/navigation";

// No separate landing/hero page (a deliberate scope call, see README) --
// the dashboard is the product.
export default function Home() {
  redirect("/dashboard");
}
