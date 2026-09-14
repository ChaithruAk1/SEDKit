import '@mantine/core/styles.css';
import './styles.css';

import { MantineProvider, createTheme } from '@mantine/core';
import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { RouterProvider } from 'react-router/dom';

import { createAppRouter } from './app/router';

const theme = createTheme({
  primaryColor: 'blue',
  defaultRadius: 'md',
  // System font stacks only: no external or bundled web fonts.
  fontFamily: 'system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif',
  fontFamilyMonospace: 'ui-monospace, "Cascadia Mono", "Segoe UI Mono", Consolas, monospace',
});

const router = createAppRouter();
const container = document.getElementById('root');
if (!container) throw new Error('#root element missing from index.html');

createRoot(container).render(
  <StrictMode>
    <MantineProvider theme={theme} defaultColorScheme="light">
      <RouterProvider router={router} />
    </MantineProvider>
  </StrictMode>,
);
