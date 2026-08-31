import { RouterProvider } from '@tanstack/react-router';
import { useEffect, useRef, useState } from 'react';
import voiceboxLogo from '@/assets/voicebox-logo.png';
import ShinyText from '@/components/ShinyText';
import { TitleBarDragRegion } from '@/components/TitleBarDragRegion';
import { TOP_SAFE_AREA_PADDING } from '@/lib/constants/ui';
import { cn } from '@/lib/utils/cn';
import { usePlatform } from '@/platform/PlatformContext';
import { router } from '@/router';
import { useServerStore } from '@/stores/serverStore';

const LOADING_MESSAGES = [
  'Preparing private storage...',
  'Starting local orchestration...',
  'Checking speech providers...',
  'Opening the audio workspace...',
  'Preparing audio pipelines...',
  'Checking provider endpoints...',
  'Tuning frequency analyzers...',
  'Preparing voice profiles...',
  'Configuring local speech services...',
  'Syncing audio buffers...',
  'Establishing model connections...',
  'Checking consent records...',
  'Validating voice samples...',
  'Validating provider contracts...',
  'Mapping phoneme sequences...',
  'Aligning prosody parameters...',
  'Activating speech synthesis...',
  'Preparing generation provenance...',
  'Preparing voice cloning matrices...',
  'Connecting to Kilix providers...',
];

function App() {
  const platform = usePlatform();
  const [serverReady, setServerReady] = useState(false);
  const [loadingMessageIndex, setLoadingMessageIndex] = useState(0);
  const serverStartingRef = useRef(false);

  // Setup lifecycle callbacks
  useEffect(() => {
    platform.lifecycle.onServerReady = () => {
      setServerReady(true);
    };
    // Empty dependency array - platform is stable from context, only run once
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [platform.lifecycle]);

  // Setup window close handler and auto-start server when running in Tauri (production only)
  useEffect(() => {
    if (!platform.metadata.isTauri) {
      setServerReady(true); // Web assumes server is running
      return;
    }

    // Only auto-start server in production mode
    // In dev mode, user runs server separately
    if (!import.meta.env?.PROD) {
      console.log('VOICEBOX_DEVELOPMENT_SERVER_EXTERNAL');
      setServerReady(true); // Mark as ready so UI doesn't show loading screen
      return;
    }

    // Auto-start server in production
    if (serverStartingRef.current) {
      return;
    }

    serverStartingRef.current = true;
    console.log('VOICEBOX_SIDECAR_STARTING');

    platform.lifecycle
      .startServer()
      .then((serverSession) => {
        console.log('VOICEBOX_SIDECAR_READY');
        useServerStore.getState().setServerSession(serverSession);
        setServerReady(true);
      })
      .catch(() => {
        serverStartingRef.current = false;
      });

    // Cleanup: stop server on actual unmount (not StrictMode remount)
    // Note: Window close is handled separately in Tauri Rust code
    return () => {
      serverStartingRef.current = false;
    };
    // Empty dependency array - platform is stable from context, only run once
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [platform.metadata.isTauri, platform.lifecycle]);

  // Cycle through loading messages every 3 seconds
  useEffect(() => {
    if (!platform.metadata.isTauri || serverReady) {
      return;
    }

    const interval = setInterval(() => {
      setLoadingMessageIndex((prev) => (prev + 1) % LOADING_MESSAGES.length);
    }, 3000);

    return () => clearInterval(interval);
  }, [serverReady, platform.metadata.isTauri]);

  // Show loading screen while server is starting in Tauri
  if (platform.metadata.isTauri && !serverReady) {
    return (
      <div
        className={cn(
          'min-h-screen bg-background flex items-center justify-center',
          TOP_SAFE_AREA_PADDING,
        )}
      >
        <TitleBarDragRegion />
        <div className="text-center space-y-6">
          <div className="flex justify-center relative">
            <div className="absolute inset-0 flex items-center justify-center">
              <div className="w-48 h-48 rounded-full bg-accent/20 blur-3xl" />
            </div>
            <img
              src={voiceboxLogo}
              alt="Voicebox"
              className="w-48 h-48 object-contain animate-fade-in-scale relative z-10"
            />
          </div>
          <div className="animate-fade-in-delayed">
            <ShinyText
              text={LOADING_MESSAGES[loadingMessageIndex]}
              className="text-lg font-medium text-muted-foreground"
              speed={2}
              color="hsl(var(--muted-foreground))"
              shineColor="hsl(var(--foreground))"
            />
          </div>
        </div>
      </div>
    );
  }

  return <RouterProvider router={router} />;
}

export default App;
