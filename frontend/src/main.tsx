import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App'
import { CognitradeProvider } from './CognitradeContext'
import './styles.css'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <CognitradeProvider>
      <App />
    </CognitradeProvider>
  </StrictMode>,
)
