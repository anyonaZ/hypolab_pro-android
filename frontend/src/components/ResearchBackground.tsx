import { lazy, Suspense, useEffect, useState } from "react";
import "./ResearchBackground.css";
const PixelBlast=lazy(()=>import("./PixelBlast"));
const Particles=lazy(()=>import("./Particles"));
const Threads=lazy(()=>import("./Threads"));
const Aurora=lazy(()=>import("./Aurora"));

export function ResearchBackground({ mode }: { mode: string }) {
  const animated = ['pixel','particles','threads','aurora','aurora-light'].includes(mode);
  const [active, setActive] = useState(true);
  const [reduceMotion, setReduceMotion] = useState(false);

  useEffect(() => {
    if (!animated) return;
    const motion = window.matchMedia('(prefers-reduced-motion: reduce)');
    let idleTimer = 0;
    const setMotion = () => setReduceMotion(motion.matches);
    const suspendLater = () => {
      window.clearTimeout(idleTimer);
      if (document.hidden) {
        setActive(false);
        return;
      }
      setActive(true);
      idleTimer = window.setTimeout(() => setActive(false), 30_000);
    };
    const visibility = () => document.hidden ? setActive(false) : suspendLater();
    const events: (keyof WindowEventMap)[] = ['pointerdown', 'pointermove', 'keydown', 'wheel', 'touchstart'];
    setMotion();
    suspendLater();
    motion.addEventListener?.('change', setMotion);
    document.addEventListener('visibilitychange', visibility);
    events.forEach(name => window.addEventListener(name, suspendLater, { passive: true }));
    return () => {
      window.clearTimeout(idleTimer);
      motion.removeEventListener?.('change', setMotion);
      document.removeEventListener('visibilitychange', visibility);
      events.forEach(name => window.removeEventListener(name, suspendLater));
    };
  }, [animated, mode]);

  if (!animated || !active || reduceMotion) return null;
  return (
    <div className="research-background" aria-hidden="true"><Suspense fallback={null}>
      {mode === "pixel" ? <PixelBlast
        variant="circle"
        pixelSize={6}
        color="#f7a23a"
        patternScale={3}
        patternDensity={1.05}
        pixelSizeJitter={0.45}
        enableRipples={false}
        liquid
        liquidStrength={0.08}
        liquidRadius={1.1}
        liquidWobbleSpeed={4}
        speed={0.32}
        edgeFade={0.2}
        transparent
      /> : null}
      {mode === "particles" ? <Particles particleColors={["#f7a23a", "#ffffff"]} particleCount={150} particleSpread={12} speed={0.045} particleBaseSize={72} alphaParticles sizeRandomness={0.8} disableRotation={false} /> : null}
      {mode === "threads" ? <Threads color={[0.97, 0.64, 0.23]} amplitude={0.55} distance={0.82} enableMouseInteraction={false} /> : null}
      {mode === "aurora" ? <Aurora colorStops={["#15b8a6","#7c67ff","#f7a23a"]} blend={0.58} amplitude={0.75} speed={0.35} /> : null}
      {mode === "aurora-light" ? <Aurora colorStops={["#c7f2e8","#cabfff","#ffd9a6"]} blend={0.7} amplitude={0.55} speed={0.25} lightMode /> : null}</Suspense>
    </div>
  );
}
