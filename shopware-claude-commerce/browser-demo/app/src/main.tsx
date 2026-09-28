// Copyright 2026 shopware AG
// SPDX-License-Identifier: MIT

import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import App from './App';
import './styles/index.css';

const rootElement = document.getElementById('root');
if (!rootElement) throw new Error('#root missing');
createRoot(rootElement).render(
  <StrictMode>
    <App />
  </StrictMode>
);
