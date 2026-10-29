import React, { useEffect, useRef } from 'react';

interface StarfieldProps {
  mode?: 'off' | 'calm' | 'full';
}

interface Star {
  x: number;
  y: number;
  size: number;
  alpha: number;
  speed: number;
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

    const handleResize = () => {
      if (!canvas) return;
      width = canvas.width = window.innerWidth;
      height = canvas.height = window.innerHeight;
    };
    window.addEventListener('resize', handleResize);

    // Create 150-250 stars
    const starCount = mode === 'full' ? 220 : 130;
    const stars: Star[] = Array.from({ length: starCount }, () => ({
      x: Math.random() * width,
      y: Math.random() * height,
      size: Math.random() * 1.5 + 0.5,
      alpha: Math.random() * 0.8 + 0.2,
      speed: Math.random() * 0.02 + 0.005,
    }));

    let shootingStar: ShootingStar | null = null;
    let lastShootingStarTime = Date.now();

    const render = () => {
      ctx.clearRect(0, 0, width, height);

      // Night sky background gradient
      const grad = ctx.createLinearGradient(0, 0, 0, height);
      grad.addColorStop(0, '#05060F');
      grad.addColorStop(1, '#0B1026');
      ctx.fillStyle = grad;
      ctx.fillRect(0, 0, width, height);

      // Soft moon glow in upper right corner
      const moonGlow = ctx.createRadialGradient(width - 80, 80, 10, width - 80, 80, 260);
      moonGlow.addColorStop(0, 'rgba(201, 212, 255, 0.12)');
      moonGlow.addColorStop(0.5, 'rgba(124, 108, 255, 0.04)');
      moonGlow.addColorStop(1, 'transparent');
      ctx.fillStyle = moonGlow;
      ctx.fillRect(width - 350, 0, 350, 350);

      // Draw and twinkle stars
      for (const s of stars) {
        s.alpha += s.speed;
        if (s.alpha > 0.95 || s.alpha < 0.15) {
          s.speed = -s.speed;
        }
        ctx.fillStyle = `rgba(230, 234, 255, ${Math.max(0.1, Math.min(1.0, s.alpha))})`;
        ctx.beginPath();
        ctx.arc(s.x, s.y, s.size, 0, Math.PI * 2);
        ctx.fill();
      }

      // Rare shooting star
      const now = Date.now();
      if (!shootingStar && now - lastShootingStarTime > (mode === 'full' ? 20000 : 35000)) {
        if (Math.random() < 0.4) {
          shootingStar = {
            x: Math.random() * width * 0.7,
            y: Math.random() * height * 0.4,
            length: Math.random() * 80 + 40,
            speed: Math.random() * 8 + 6,
            angle: Math.PI / 4 + (Math.random() * 0.2 - 0.1),
            opacity: 1.0,
          };
          lastShootingStarTime = now;
        }
      }

      if (shootingStar) {
        ctx.strokeStyle = `rgba(201, 212, 255, ${shootingStar.opacity})`;
        ctx.lineWidth = 1.5;
        ctx.beginPath();
        ctx.moveTo(shootingStar.x, shootingStar.y);
        ctx.lineTo(
          shootingStar.x + Math.cos(shootingStar.angle) * shootingStar.length,
          shootingStar.y + Math.sin(shootingStar.angle) * shootingStar.length
        );
        ctx.stroke();

        shootingStar.x += Math.cos(shootingStar.angle) * shootingStar.speed;
        shootingStar.y += Math.sin(shootingStar.angle) * shootingStar.speed;
        shootingStar.opacity -= 0.015;

        if (shootingStar.opacity <= 0) {
          shootingStar = null;
        }
      }

      animationFrameId = requestAnimationFrame(render);
    };

    animationFrameId = requestAnimationFrame(render);

    return () => {
      cancelAnimationFrame(animationFrameId);
      window.removeEventListener('resize', handleResize);
    };
  }, [mode]);

  if (mode === 'off') {
    return <div className="fixed inset-0 pointer-events-none bg-background z-[-1]" />;
  }

  return (
    <canvas
      ref={canvasRef}
      className="fixed inset-0 pointer-events-none z-[-1] transition-opacity duration-500"
    />
  );
};
