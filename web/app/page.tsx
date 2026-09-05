import {
  CTA,
  FAQ,
  Footer,
  HardCase,
  Hero,
  HowItWorks,
  LiveStats,
  Nav,
  Problem,
  Results,
} from "@/components/landing/sections"

export default function Landing() {
  return (
    <div className="min-h-screen bg-background">
      <Nav />
      <main>
        <Hero />
        <LiveStats />
        <Problem />
        <HowItWorks />
        <HardCase />
        <Results />
        <FAQ />
        <CTA />
      </main>
      <Footer />
    </div>
  )
}
