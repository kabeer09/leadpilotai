import { Routes, Route, Navigate } from 'react-router-dom';
import { useAuth } from './auth';
import Layout from './Layout';
import Landing from './pages/Landing';
import { Login, Signup } from './pages/AuthPages';
import { Forgot, Reset } from './pages/PasswordPages';
import Dashboard from './pages/Dashboard';
import Companies from './pages/Companies';
import CompanyDetail from './pages/CompanyDetail';
import Leads from './pages/Leads';
import LeadDetail from './pages/LeadDetail';
import Crawling from './pages/Crawling';
import Campaigns from './pages/Campaigns';
import CampaignDetail from './pages/CampaignDetail';
import WhatsApp from './pages/WhatsApp';
import EmailActivity from './pages/EmailActivity';
import Followups from './pages/Followups';
import Analytics from './pages/Analytics';
import Settings from './pages/Settings';
import Sources from './pages/Sources';
import Research from './pages/Research';
import Unsubscribe from './pages/Unsubscribe';
import { GoogleCallback } from './pages/GoogleAuth';

function Guard({ children }: { children: JSX.Element }) {
  const { user, loading } = useAuth();
  if (loading) return <p className="p-8 text-gray-500">Loading…</p>;
  if (!user) return <Navigate to="/login" replace />;
  return children;
}

export default function App() {
  return (
    <Routes>
      <Route path="/landing" element={<Landing />} />
      <Route path="/login" element={<Login />} />
      <Route path="/signup" element={<Signup />} />
      <Route path="/forgot" element={<Forgot />} />
      <Route path="/reset" element={<Reset />} />
      <Route path="/unsubscribe" element={<Unsubscribe />} />
      <Route path="/auth/callback" element={<GoogleCallback />} />
      <Route path="/" element={<Guard><Layout /></Guard>}>
        <Route index element={<Dashboard />} />
        <Route path="companies" element={<Companies />} />
        <Route path="companies/:id" element={<CompanyDetail />} />
        <Route path="leads" element={<Leads />} />
        <Route path="leads/:id" element={<LeadDetail />} />
        <Route path="crawling" element={<Crawling />} />
        <Route path="campaigns" element={<Campaigns />} />
        <Route path="campaigns/:id" element={<CampaignDetail />} />
        <Route path="whatsapp" element={<WhatsApp />} />
        <Route path="activity" element={<EmailActivity />} />
        <Route path="followups" element={<Followups />} />
        <Route path="analytics" element={<Analytics />} />
        <Route path="settings" element={<Settings />} />
        <Route path="sources" element={<Sources />} />
        <Route path="research" element={<Research />} />
      </Route>
      <Route path="*" element={<Landing />} />
    </Routes>
  );
}
