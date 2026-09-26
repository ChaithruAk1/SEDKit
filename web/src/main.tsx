import '@mantine/core/styles.css';
import './design/index.css';
import './styles.css';

import { MantineProvider } from '@mantine/core';
import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { RouterProvider } from 'react-router/dom';

import { createAppRouter } from './app/router';
import { AuthGate } from './core/auth/AuthGate';
import { cssVariablesResolver, theme } from './design/theme';

const router = createAppRouter();
const container = document.getElementById('root');
if (!container) throw new Error('#root element missing from index.html');

createRoot(container).render(
  <StrictMode>
    <MantineProvider theme={theme} cssVariablesResolver={cssVariablesResolver} defaultColorScheme="light">
      <AuthGate>
        <RouterProvider router={router} />
      </AuthGate>
    </MantineProvider>
  </StrictMode>,
);
