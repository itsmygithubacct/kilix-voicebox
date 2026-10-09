import React from 'react';
import ReactDOM from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import App from '../../app/src/App';
import '../../app/src/index.css';
import { PlatformProvider } from '../../app/src/platform/PlatformContext';
import { webPlatform } from './platform';
import { useServerStore } from '../../app/src/stores/serverStore';

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 1000 * 60 * 5, // 5 minutes
      gcTime: 1000 * 60 * 10, // 10 minutes
      retry: 1,
      refetchOnWindowFocus: false,
    },
  },
});

async function start() {
  const root = ReactDOM.createRoot(document.getElementById('root')!);
  try {
    const session = await webPlatform.lifecycle.startServer();
    useServerStore.getState().setServerSession(session);
  } catch {
    root.render(<p role="alert">Voicebox could not authenticate this window. Close it and reopen Voicebox.</p>);
    return;
  }
  root.render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <PlatformProvider platform={webPlatform}>
        <App />
      </PlatformProvider>
    </QueryClientProvider>
  </React.StrictMode>,
);
}

void start();
