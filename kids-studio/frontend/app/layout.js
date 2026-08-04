import "./globals.css";

export const metadata = {
  title: "Kids Studio",
  description: "AI kids' story video pipeline and multi-platform publishing dashboard",
};

export default function RootLayout({ children }) {
  return (
    <html lang="en" className="h-full antialiased">
      <body className="min-h-full flex flex-col">{children}</body>
    </html>
  );
}
