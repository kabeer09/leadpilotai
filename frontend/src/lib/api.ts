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
export const generateDraft = (id: number, d?: object) => api.post('/research/draft/' + id, d || {}).then((r) => r.data);
export const listQualified = (minScore = 40, refresh = false) =>
  api.get('/research/qualified', { params: { min_score: minScore, refresh } }).then((r) => r.data);
export const runResearch = (d: object) => api.post('/research/run', d).then((r) => r.data);
export const researchPickCompanies = (p: Record<string, string | number | boolean>) =>
  api.get('/research/companies', { params: p }).then((r) => r.data);
export const researchReport = (id: number) =>
  api.get(`/research/report/${id}`).then((r) => r.data);
export const researchReportRefresh = (id: number) =>
  api.post(`/research/report/${id}/refresh`).then((r) => r.data);

// Buyer/Supplier Discovery (classified over real workspace data)
export const discoveryBuyers = (p: Record<string, string | number | boolean>) =>
  api.get('/discovery/buyers', { params: p }).then((r) => r.data);
export const discoveryExport = (p: Record<string, string | number | boolean>) =>
  api.get('/discovery/export', { params: p, responseType: 'blob' })
    .then((r) => r.data as Blob);
export const discoverySavedList = () =>
  api.get('/discovery/saved-searches').then((r) => r.data);
export const discoverySavedCreate = (d: object) =>
  api.post('/discovery/saved-searches', d).then((r) => r.data);
export const discoverySavedDelete = (id: number) =>
  api.delete(`/discovery/saved-searches/${id}`).then((r) => r.data);

// Exhibitor dashboard (extraction status + search + export + launch)
export const exhibitorsList = (p: Record<string, string | number | boolean>) =>
  api.get('/exhibitors', { params: p }).then((r) => r.data);
export const exhibitorsOverview = () =>
  api.get('/exhibitors/overview').then((r) => r.data);
export const exhibitorsExport = (p: Record<string, string | number | boolean>) =>
  api.get('/exhibitors/export', { params: p, responseType: 'blob' })
    .then((r) => r.data as Blob);
export const exhibitorsExtract = (d: object) =>
  api.post('/exhibitors/extract', d).then((r) => r.data);

// Verification
export const verifyEmail = (d: object) => api.post('/verify/email', d).then((r) => r.data);
export const verifyBatch = (d: object) => api.post('/verify/batch', d).then((r) => r.data);
export const geminiStatus = () => api.get('/verify/gemini-status').then((r) => r.data);

// Enrichment
export const enrichWebsite = (id: number) => api.post('/enrich/website/' + id, {}).then((r) => r.data);
export const enrichBatch = (d: object) => api.post('/enrich/batch', d).then((r) => r.data);

// Google Business Lead Finder
export const gbCreateCampaign = (d: object) => api.post('/gbleads/campaigns', d).then((r) => r.data);
export const gbListCampaigns = () => api.get('/gbleads/campaigns').then((r) => r.data);
export const gbGetCampaign = (id: number) => api.get(`/gbleads/campaigns/${id}`).then((r) => r.data);
export const gbCancelCampaign = (id: number) => api.post(`/gbleads/campaigns/${id}/cancel`).then((r) => r.data);
export const gbPauseCampaign = (id: number) => api.post(`/gbleads/campaigns/${id}/pause`).then((r) => r.data);
export const gbResumeCampaign = (id: number) => api.post(`/gbleads/campaigns/${id}/resume`).then((r) => r.data);
export const gbListLeads = (p: Record<string, string | number | boolean>) =>
  api.get('/gbleads/leads', { params: p }).then((r) => r.data);
export const gbGetLead = (id: number) => api.get(`/gbleads/leads/${id}`).then((r) => r.data);
export const gbUpdateLead = (id: number, d: object) => api.patch(`/gbleads/leads/${id}`, d).then((r) => r.data);
export const gbEnrich = (d: object) => api.post('/gbleads/enrich', d).then((r) => r.data);
export const gbSaveToCompanies = (d: object) => api.post('/gbleads/save-to-companies', d).then((r) => r.data);
export const gbListJobs = () => api.get('/gbleads/jobs').then((r) => r.data);
export const gbCancelJob = (id: number) => api.post(`/gbleads/jobs/${id}/cancel`).then((r) => r.data);
export const gbRetryJob = (id: number) => api.post(`/gbleads/jobs/${id}/retry`).then((r) => r.data);
export const gbSettings = () => api.get('/gbleads/settings').then((r) => r.data);
// Authenticated download: fetch as blob (a plain <a> link can't carry the
// Authorization header), then save client-side.
export const gbExport = (p: Record<string, string | number | boolean>) =>
  api.get('/gbleads/export', { params: p, responseType: 'blob' }).then((r) => r.data as Blob);

// Global Trade Intelligence (UN Comtrade — aggregated statistics, not shipments)
export const tradeSearch = (p: Record<string, string | number>) =>
  api.get('/trade/search', { params: p }).then((r) => r.data);
export const tradeStatistics = (p: Record<string, string | number>) =>
  api.get('/trade/statistics', { params: p }).then((r) => r.data);
export const tradeHsCodes = (p: Record<string, string | number>) =>
  api.get('/trade/hs-codes', { params: p }).then((r) => r.data);
export const tradeCountries = () => api.get('/trade/countries').then((r) => r.data);
export const tradeListSaved = () => api.get('/trade/saved-searches').then((r) => r.data);
export const tradeSaveSearch = (d: object) =>
  api.post('/trade/saved-searches', d).then((r) => r.data);
export const tradeDeleteSaved = (id: number) =>
  api.delete(`/trade/saved-searches/${id}`).then((r) => r.data);
export const tradeExport = (p: Record<string, string | number>, format: 'csv' | 'xlsx') =>
  api.get('/trade/export', { params: { ...p, format }, responseType: 'blob' })
    .then((r) => r.data as Blob);
export const dataSources = () => api.get('/data-sources').then((r) => r.data);
// Phase 4: competitor analysis, HS intelligence, country comparison
export const tradeCompetitors = (p: Record<string, string | number>) =>
  api.get('/trade/competitors', { params: p }).then((r) => r.data);
export const tradeHsIntel = (p: Record<string, string | number>) =>
  api.get('/trade/hs-intel', { params: p }).then((r) => r.data);
export const tradeCompare = (p: Record<string, string | number>) =>
  api.get('/trade/compare', { params: p }).then((r) => r.data);

// Analytics + health
export const overview = (days = 30) => api.get('/analytics/overview', { params: { days } }).then((r) => r.data);
export const trends = (days = 30) => api.get('/analytics/trends', { params: { days } }).then((r) => r.data);
export const health = () => api.get('/health').then((r) => r.data);
