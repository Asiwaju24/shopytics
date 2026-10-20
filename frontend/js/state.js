// Shared session state + role permissions (mirrors the rules enforced by the API).
export const state = {
  user: null,
  stores: [],
  store: null,
  role: null,
  memberships: [],
};

const R = { OWNER: 'OWNER', MANAGER: 'MANAGER', CASHIER: 'CASHIER', ACCOUNTANT: 'ACCOUNTANT', VIEWER: 'VIEWER' };

const PERMISSIONS = {
  sell: [R.OWNER, R.MANAGER, R.CASHIER],
  void: [R.OWNER, R.MANAGER],
  products: [R.OWNER, R.MANAGER],
  purchases: [R.OWNER, R.MANAGER],
  suppliers: [R.OWNER, R.MANAGER],
  customers: [R.OWNER, R.MANAGER, R.CASHIER],
  debts: [R.OWNER, R.MANAGER, R.ACCOUNTANT],
  recordPayment: [R.OWNER, R.MANAGER, R.ACCOUNTANT, R.CASHIER],
  finance: [R.OWNER, R.MANAGER, R.ACCOUNTANT],
  importData: [R.OWNER, R.MANAGER, R.CASHIER],
  team: [R.OWNER],
  settings: [R.OWNER],
};

export function can(action) {
  return (PERMISSIONS[action] || []).includes(state.role);
}

export const ROLE_LABELS = {
  OWNER: 'Owner', MANAGER: 'Manager', CASHIER: 'Cashier', ACCOUNTANT: 'Accountant', VIEWER: 'Viewer',
};
