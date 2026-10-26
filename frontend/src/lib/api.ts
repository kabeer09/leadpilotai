import axios from 'axios';

const api = axios.create({ baseURL: '/api', headers: { 'Content-Type': 'application/json' } });

api.interceptors.request.use((cfg) => {
  const t = localStorage.getItem('lp_access');
  if (t) cfg.headers.Authorization = `Bearer ${t}`;
  return cfg;
});

let refreshing = false;
api.interceptors.response.use(
  (r) => r,
  async (err) => {
    const orig = err.config;
    if (err.response?.status === 401 && !orig._retried && localStorage.getItem('lp_refresh')) {
      orig._retried = true;
      if (!refreshing) {
        refreshing = true;
        try {
          const r = await axios.post('/api/auth/refresh', {
            refresh_token: localStorage.getItem('lp_refresh'),
          });
          localStorage.setItem('lp_access', r.data.access_token);
          localStorage.setItem('lp_refresh', r.data.refresh_token);
        } catch {
          localStorage.removeItem('lp_access');
          localStorage.removeItem('lp_refresh');
          window.location.href = '/login';
          return Promise.reject(err);
        } finally {
          refreshing = false;
        }
      }
      orig.headers.Authorization = `Bearer ${localStorage.getItem('lp_access')}`;
      return api(orig);
    }
    return Promise.reject(err);
  }
);

export default api;

// Auth
export const signup = (d: object) => api.post('/auth/signup', d).then((r) => r.data);
export const login = (d: object) => api.post('/auth/login', d).then((r) => r.data);
export const sendOTP = (d: object) => api.post('/auth/otp/send', d).then((r) => r.data);
export const verifyOTP = (d: object) => api.post('/auth/otp/verify', d).then((r) => r.data);
export const googleLoginUrl = () => api.get('/auth/google/login').then((r) => r.data);
export const googleConsume = (code: string) => api.post('/auth/google/consume', { code }).then((r) => r.data);
export const gmailStatus = () => api.get('/auth/google/gmail/status').then((r) => r.data);
export const logout = () =>
  api.post('/auth/logout', { refresh_token: localStorage.getItem('lp_refresh') }).then((r) => r.data);
export const me = () => api.get('/auth/me').then((r) => r.data);
export const requestReset = (email: string) =>
  api.post('/auth/password-reset/request', { email }).then((r) => r.data);
export const confirmReset = (d: object) =>
  api.post('/auth/password-reset/confirm', d).then((r) => r.data);

// Crawls
export const createCrawl = (d: object) => api.post('/crawls', d).then((r) => r.data);
export const listCrawls = () => api.get('/crawls').then((r) => r.data);
export const getCrawl = (id: number) => api.get(`/crawls/${id}`).then((r) => r.data);
export const cancelCrawl = (id: number) => api.post(`/crawls/${id}/cancel`).then((r) => r.data);

// CRM
export const listCompanies = (p: Record<string, string | number>) =>
  api.get('/companies', { params: p }).then((r) => r.data);
export const getCompany = (id: number) => api.get(`/companies/${id}`).then((r) => r.data);
export const listLeads = (p: Record<string, string | number | boolean>) =>
  api.get('/leads', { params: p }).then((r) => r.data);
export const getLead = (id: number) => api.get(`/leads/${id}`).then((r) => r.data);
export const updateLead = (id: number, d: object) => api.patch(`/leads/${id}`, d).then((r) => r.data);
export const getDuplicates = () => api.get('/duplicates').then((r) => r.data);
export const mergePreview = (a: number, b: number) =>
  api.post('/companies/merge-preview', { keep_id: a, duplicate_id: b }).then((r) => r.data);
export const mergeApply = (a: number, b: number) =>
  api.post('/companies/merge', { keep_id: a, duplicate_id: b }).then((r) => r.data);

// Campaigns + email
export const listCampaigns = () => api.get('/campaigns').then((r) => r.data);
export const createCampaign = (d: object) => api.post('/campaigns', d).then((r) => r.data);
export const campaignPreview = (id: number) => api.get(`/campaigns/${id}/preview`).then((r) => r.data);
export const campaignTest = (id: number, d: object) => api.post(`/campaigns/${id}/test`, d).then((r) => r.data);
export const campaignSend = (id: number, d: object) => api.post(`/campaigns/${id}/send`, d).then((r) => r.data);
export const emailEvents = (p?: object) => api.get('/email-events', { params: p }).then((r) => r.data);
export const inboundEvent = (d: object) => api.post('/email-events/inbound', d).then((r) => r.data);
export const listFollowups = () => api.get('/followups').then((r) => r.data);
export const processFollowups = () => api.post('/followups/process').then((r) => r.data);
export const listSuppressions = () => api.get('/suppressions').then((r) => r.data);

// WhatsApp
export const whatsappLeads = () => api.get('/whatsapp/leads').then((r) => r.data);
export const whatsappSend = (d: object) => api.post('/whatsapp/send', d).then((r) => r.data);

// Sources & Import
export const listSources = () => api.get('/sources').then((r) => r.data);
export const importTemplate = () => api.get('/import/template').then((r) => r.data);
export const importCSV = (file: File, sourceName?: string) => {
  const fd = new FormData();
  fd.append('file', file);
  return api.post('/import/csv' + (sourceName ? '?source_name=' + encodeURIComponent(sourceName) : ''), fd, {
    headers: { 'Content-Type': 'multipart/form-data' },
  }).then((r) => r.data);
};
export const importManualUrl = (d: object) => api.post('/import/manual-url', d).then((r) => r.data);

// Research
export const researchCompany = (id: number) => api.get('/research/company/' + id).then((r) => r.data);
export const generateDraft = (id: number) => api.post('/research/draft/' + id, {}).then((r) => r.data);
export const listQualified = (minScore = 40) => api.get('/research/qualified', { params: { min_score: minScore } }).then((r) => r.data);
export const runResearch = (d: object) => api.post('/research/run', d).then((r) => r.data);

// Enrichment
export const enrichWebsite = (id: number) => api.post('/enrich/website/' + id, {}).then((r) => r.data);
export const enrichBatch = (d: object) => api.post('/enrich/batch', d).then((r) => r.data);

// Analytics + health
export const overview = (days = 30) => api.get('/analytics/overview', { params: { days } }).then((r) => r.data);
export const trends = (days = 30) => api.get('/analytics/trends', { params: { days } }).then((r) => r.data);
export const health = () => api.get('/health').then((r) => r.data);
