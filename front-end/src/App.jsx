import { Routes, Route, Navigate } from 'react-router-dom'
import Login from './pages/Login.jsx'
import Capture from './pages/Capture.jsx'
import Replay from './pages/Replay.jsx'

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<Login />} />
      <Route path="/login" element={<Login />} />
      <Route path="/capture" element={<Capture />} />
      <Route path="/replay" element={<Replay />} />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  )
}
