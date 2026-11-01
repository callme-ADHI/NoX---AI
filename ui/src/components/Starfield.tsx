import React, { useEffect, useRef } from 'react';

interface StarfieldProps {
  mode?: 'off' | 'calm' | 'full';
}

interface CosmicStar {
  x: number;
  y: number;
  baseRadius: number;
  baseAlpha: number;
  twinkleFreq: number;
  phase: number;
  color: string;
  hasHalo: boolean;
}

interface ShootingStar {
  x: number;
  y: number;
  length: number;
  speed: number;
  angle: number;
  opacity: number;
}

export const Starfield: React.FC<StarfieldProps> = ({ mode = 'calm' }) => {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);

  useEffect(() => {
    if (mode === 'off') return;

    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    let animationFrameId: number;
    let width = (canvas.width = window.innerWidth);
    let height = (canvas.height = window.innerHeight);

    // Mouse coordinates for gentle cosmic proximity effect
    let mouseX = -1000;
    let mouseY = -1000;

    const handleMouseMove = (e: MouseEvent) => {
      mouseX = e.clientX;
      mouseY = e.clientY;
    };
    window.addEventListener('mousemove', handleMouseMove, { passive: true });

    const handleResize = () => {
      if (!canvas) return;
      width = canvas.width = window.innerWidth;
      height = canvas.height = window.innerHeight;
    };
    window.addEventListener('resize', handleResize);

    // Color temperatures of stars in deep night sky
    const starSpectra = [
      'rgba(255, 255, 255, ',    // Diamond Ice White (60%)
      'rgba(255, 255, 255, ',
      'rgba(255, 255, 255, ',
      'rgba(186, 230, 253, ',    // Sirius Starlight Blue (20%)
      'rgba(224, 231, 255, ',    // Vega Cosmic Violet (10%)
      'rgba(254, 243, 199, ',    // Capella Golden Starlight (10%)
    ];

    const totalStars = mode === 'full' ? 240 : 160;
    const stars: CosmicStar[] = Array.from({ length: totalStars }, (_, i) => {
      // 10% foreground luminous stars with halos
      const isForeground = i < Math.floor(totalStars * 0.1);
      const isMidfield = !isForeground && i < Math.floor(totalStars * 0.4);

      let radius = Math.random() * 0.35 + 0.45; // Distant micro-stars: 0.45 - 0.8px
      if (isMidfield) {
        radius = Math.random() * 0.5 + 0.85; // Midfield: 0.85 - 1.35px
      } else if (isForeground) {
        radius = Math.random() * 0.7 + 1.5;  // Luminous beacons: 1.5 - 2.2px
      }

      return {
        x: Math.random() * width,
        y: Math.random() * height,
        baseRadius: radius,
        baseAlpha: isForeground ? 0.75 : isMidfield ? 0.55 : 0.35,
        twinkleFreq: Math.random() * 0.003 + 0.0015,
        phase: Math.random() * Math.PI * 2,
        color: starSpectra[Math.floor(Math.random() * starSpectra.length)],
        hasHalo: isForeground,
      };
    });

    const prefersReducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

    let shootingStar: ShootingStar | null = null;
    let lastShootingStarTime = Date.now();
    let lastFrameTime = 0;
    const frameInterval = 1000 / 30; // 30 FPS cap

    const drawScene = (timestamp: number) => {
      ctx.clearRect(0, 0, width, height);

      // 1. Deep cosmic void pitch-black base (#000000)
      ctx.fillStyle = '#000000';
      ctx.fillRect(0, 0, width, height);

      // 2. Ethereal ambient nebulae (very subtle cosmic dust)
      const nebula1 = ctx.createRadialGradient(width * 0.85, height * 0.15, 20, width * 0.85, height * 0.15, width * 0.5);
      nebula1.addColorStop(0, 'rgba(129, 140, 248, 0.038)');
      nebula1.addColorStop(0.5, 'rgba(99, 102, 241, 0.015)');
      nebula1.addColorStop(1, 'transparent');
      ctx.fillStyle = nebula1;
      ctx.fillRect(0, 0, width, height);

      const nebula2 = ctx.createRadialGradient(width * 0.15, height * 0.65, 30, width * 0.15, height * 0.65, width * 0.45);
      nebula2.addColorStop(0, 'rgba(56, 189, 248, 0.028)');
      nebula2.addColorStop(0.6, 'rgba(14, 165, 233, 0.010)');
      nebula2.addColorStop(1, 'transparent');
      ctx.fillStyle = nebula2;
      ctx.fillRect(0, 0, width, height);

      // 3. Render and animate stars
      for (const s of stars) {
        const twinkle = prefersReducedMotion ? 0 : Math.sin(timestamp * s.twinkleFreq + s.phase);
        let alpha = s.baseAlpha + twinkle * 0.3;

        const dx = s.x - mouseX;
        const dy = s.y - mouseY;
        const distSq = dx * dx + dy * dy;
        if (distSq < 14400 && !prefersReducedMotion) {
          const proximity = 1 - Math.sqrt(distSq) / 120;
          alpha += proximity * 0.35;
        }

        alpha = Math.max(0.12, Math.min(1.0, alpha));

        if (s.hasHalo && alpha > 0.6) {
          const halo = ctx.createRadialGradient(s.x, s.y, 0, s.x, s.y, s.baseRadius * 4.5);
          halo.addColorStop(0, `${s.color}${alpha * 0.45})`);
          halo.addColorStop(0.4, `${s.color}${alpha * 0.15})`);
          halo.addColorStop(1, 'transparent');
          ctx.fillStyle = halo;
          ctx.beginPath();
          ctx.arc(s.x, s.y, s.baseRadius * 4.5, 0, Math.PI * 2);
          ctx.fill();
        }

        ctx.fillStyle = `${s.color}${alpha})`;
        ctx.beginPath();
        ctx.arc(s.x, s.y, s.baseRadius, 0, Math.PI * 2);
        ctx.fill();
      }

      // 4. Elegant shooting star (disabled under reduced motion)
      if (!prefersReducedMotion) {
        const now = Date.now();
        if (!shootingStar && now - lastShootingStarTime > (mode === 'full' ? 18000 : 30000)) {
          if (Math.random() < 0.35) {
            shootingStar = {
              x: Math.random() * width * 0.8,
              y: Math.random() * height * 0.35,
              length: Math.random() * 90 + 50,
              speed: Math.random() * 9 + 7,
              angle: Math.PI / 4 + (Math.random() * 0.2 - 0.1),
              opacity: 1.0,
            };
            lastShootingStarTime = now;
          }
        }

        if (shootingStar) {
          const headX = shootingStar.x;
          const headY = shootingStar.y;
          const tailX = headX - Math.cos(shootingStar.angle) * shootingStar.length;
          const tailY = headY - Math.sin(shootingStar.angle) * shootingStar.length;

          const tailGrad = ctx.createLinearGradient(tailX, tailY, headX, headY);
          tailGrad.addColorStop(0, 'rgba(56, 189, 248, 0)');
          tailGrad.addColorStop(0.6, `rgba(56, 189, 248, ${shootingStar.opacity * 0.4})`);
          tailGrad.addColorStop(1, `rgba(255, 255, 255, ${shootingStar.opacity})`);

          ctx.strokeStyle = tailGrad;
          ctx.lineWidth = 1.6;
          ctx.lineCap = 'round';
          ctx.beginPath();
          ctx.moveTo(tailX, tailY);
          ctx.lineTo(headX, headY);
          ctx.stroke();

          ctx.fillStyle = `rgba(255, 255, 255, ${shootingStar.opacity})`;
          ctx.beginPath();
          ctx.arc(headX, headY, 1.8, 0, Math.PI * 2);
          ctx.fill();

          shootingStar.x += Math.cos(shootingStar.angle) * shootingStar.speed;
          shootingStar.y += Math.sin(shootingStar.angle) * shootingStar.speed;
          shootingStar.opacity -= 0.016;

          if (shootingStar.opacity <= 0) {
            shootingStar = null;
          }
        }
      }
    };

    const render = (timestamp: number) => {
      if (document.hidden) return;

      animationFrameId = requestAnimationFrame(render);

      const elapsed = timestamp - lastFrameTime;
      if (elapsed < frameInterval) return;
      lastFrameTime = timestamp - (elapsed % frameInterval);

      drawScene(timestamp);
    };

    if (prefersReducedMotion) {
      // Draw static stars once without animation loop
      drawScene(0);
    } else {
      animationFrameId = requestAnimationFrame(render);
    }

    const handleVisibility = () => {
      if (document.hidden) {
        cancelAnimationFrame(animationFrameId);
      } else if (!prefersReducedMotion) {
        lastFrameTime = performance.now();
        animationFrameId = requestAnimationFrame(render);
      }
    };
    document.addEventListener('visibilitychange', handleVisibility);

    return () => {
      cancelAnimationFrame(animationFrameId);
      window.removeEventListener('resize', handleResize);
      window.removeEventListener('mousemove', handleMouseMove);
      document.removeEventListener('visibilitychange', handleVisibility);
    };
  }, [mode]);

  if (mode === 'off') {
    return <div className="fixed inset-0 pointer-events-none bg-black z-[-1]" />;
  }

  return (
    <canvas
      ref={canvasRef}
      className="fixed inset-0 pointer-events-none z-[-1] transition-opacity duration-700"
    />
  );
};
