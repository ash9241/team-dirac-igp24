import type { Metadata } from "next";
import { headers } from "next/headers";
import "katex/dist/katex.min.css";
import "./globals.css";

const title = "Two Batchmates Walk Into a Maths Competition";
const description = "A founder building quantum sensors and a future category theorist used GPT‑5.6 Pro, Codex, and a search harness to reach 14th in IGP24.";

export async function generateMetadata(): Promise<Metadata> {
  const requestHeaders = await headers();
  const host = firstForwardedValue(requestHeaders.get("x-forwarded-host")) ??
    firstForwardedValue(requestHeaders.get("host")) ?? "localhost:3000";
  const forwardedProtocol = firstForwardedValue(requestHeaders.get("x-forwarded-proto"));
  const protocol = forwardedProtocol === "http" || forwardedProtocol === "https"
    ? forwardedProtocol : host.startsWith("localhost") || host.startsWith("127.0.0.1") ? "http" : "https";
  const socialImage = new URL("/images/hero-wire-knot.png", protocol + "://" + host).toString();
  return {
    title, description, authors: [{name:"Aishwarya Das"}],
    openGraph: {
      title, description, type: "article",
      images: [{url:socialImage,width:1672,height:941,type:"image/png",alt:"Entangled silver and blue strands against a dark background."}],
    },
    twitter: {card:"summary_large_image",title,description,images:[{url:socialImage,alt:"Entangled silver and blue strands against a dark background."}]},
  };
}
function firstForwardedValue(value:string|null):string|null {
  return value?.split(",",1)[0]?.trim() || null;
}
export default function RootLayout({children}:Readonly<{children:React.ReactNode}>) {
  return <html lang="en"><body>{children}</body></html>;
}
