import "./globals.css";

export const metadata = {
  title: "ChillMind — A little more clarity",
  description: "A calmer way to find answers about your ChillMind experience. Clear, grounded guidance when you need it.",
  applicationName: "ChillMind",
  robots: { index: true, follow: true },
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return <html lang="en"><body>{children}</body></html>;
}
