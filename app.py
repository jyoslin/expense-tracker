import streamlit as st
import hmac
from supabase import create_client, Client
import pandas as pd
from datetime import datetime, date, timedelta
from dateutil.relativedelta import relativedelta

# --- 1. SECURITY & SETUP ---
st.set_page_config(page_title="My Finance", page_icon="💰", layout="wide")

def check_password():
    """Returns `True` if the user had the correct password."""
    def password_entered():
        if hmac.compare_digest(st.session_state["password"], st.secrets["APP_PASSWORD"]):
            st.session_state["password_correct"] = True
            del st.session_state["password"]
        else:
            st.session_state["password_correct"] = False

    if st.session_state.get("password_correct", False):
        return True

    st.text_input("🔒 Please enter your password", type="password", on_change=password_entered, key="password")
    if "password_correct" in st.session_state and not st.session_state["password_correct"]:
        st.error("😕 Password incorrect")
    return False

if not check_password():
    st.stop()

# Connect to Supabase
url = st.secrets["SUPABASE_URL"]
key = st.secrets["SUPABASE_KEY"]
supabase: Client = create_client(url, key)

# --- 2. HELPER FUNCTIONS ---
def get_accounts(show_inactive=False):
    """Fetch accounts sorted by Custom Sort Order, then Name"""
    accounts = supabase.table('accounts').select("*").execute().data
    df = pd.DataFrame(accounts)
    
    cols = ['id', 'name', 'type', 'balance', 'include_net_worth', 'is_liquid_asset', 'goal_amount', 'goal_date', 'sort_order', 'is_active', 'remark']
    if df.empty: return pd.DataFrame(columns=cols)
    
    # Ensure columns exist
    if 'sort_order' not in df.columns: df['sort_order'] = 99
    if 'is_active' not in df.columns: df['is_active'] = True
    if 'remark' not in df.columns: df['remark'] = ""
    
    if not show_inactive:
        df = df[df['is_active'] == True]
        
    return df.sort_values(by=['sort_order', 'name'])

def get_categories(type_filter=None):
    """Fetch categories"""
    query = supabase.table('categories').select("*")
    if type_filter:
        query = query.eq('type', type_filter)
    data = query.execute().data
    if not data: return []
    return sorted([item['name'] for item in data])

def update_balance(account_id, amount_change):
    if not account_id: return 
    current = supabase.table('accounts').select("balance").eq("id", account_id).execute().data[0]['balance']
    new_balance = float(current) + float(amount_change)
    supabase.table('accounts').update({"balance": new_balance}).eq("id", account_id).execute()

def update_account_settings(id, name, balance, include_nw, is_asset, goal_amt, goal_date, sort_order, is_active, remark):
    """Save account preferences including Remark"""
    data = {
        "name": name,
        "balance": balance,
        "include_net_worth": include_nw,
        "is_liquid_asset": is_asset,
        "goal_amount": goal_amt,
        "goal_date": str(goal_date) if goal_date else None,
        "sort_order": sort_order,
        "is_active": is_active,
        "remark": remark
    }
    supabase.table('accounts').update(data).eq("id", id).execute()

def add_transaction(date, amount, description, type, from_acc_id, to_acc_id, category, remark):
    # Record
    supabase.table('transactions').insert({
        "date": str(date), "amount": amount, "description": description, "type": type,
        "from_account_id": from_acc_id, "to_account_id": to_acc_id, "category": category,
        "remark": remark
    }).execute()

    # Update Balances
    if type == "Expense": update_balance(from_acc_id, -amount)
    elif type in ["Income", "Refund"]: update_balance(to_acc_id, amount)
    elif type in ["Transfer", "Custodial In"]:
        update_balance(from_acc_id, -amount)
        update_balance(to_acc_id, amount)
    elif type == "Custodial Out":
        if from_acc_id: update_balance(from_acc_id, -amount) 
        update_balance(to_acc_id, amount)

def run_scheduled_transactions():
    """Auto-run due transactions"""
    today = datetime.today().date()
    tasks = supabase.table('schedule').select("*").lte('next_run_date', str(today)).eq('is_manual', False).execute().data
    count = 0
    if tasks:
        for task in tasks:
            cat = task.get('category', 'Recurring')
            add_transaction(task['next_run_date'], task['amount'], f"🔄 {task['description']}", 
                          task['type'], task['from_account_id'], task['to_account_id'], cat, "Auto-Scheduled")
            
            if task['frequency'] == 'Monthly':
                next_date = datetime.strptime(task['next_run_date'], '%Y-%m-%d').date() + relativedelta(months=1)
                supabase.table('schedule').update({"next_run_date": str(next_date)}).eq("id", task['id']).execute()
            else:
                supabase.table('schedule').delete().eq("id", task['id']).execute()
            count += 1
    return count

def get_due_manual_tasks():
    today = datetime.today().date()
    return supabase.table('schedule').select("*").lte('next_run_date', str(today)).eq('is_manual', True).execute().data

# --- 3. APP INTERFACE ---
st.title("💰 My Wealth Manager")

processed = run_scheduled_transactions()
if processed: st.toast(f"Processed {processed} auto-payments!", icon="🤖")

manual_due = get_due_manual_tasks()
if manual_due:
    st.warning(f"🔔 You have {len(manual_due)} manual transfers due!")

df_active = get_accounts(show_inactive=False)
account_map = dict(zip(df_active['name'], df_active['id']))
account_list = df_active['name'].tolist() if not df_active.empty else []
non_loan_accounts = df_active[df_active['type'] != 'Loan']['name'].tolist() if not df_active.empty else []

tab1, tab2, tab3, tab4, tab5 = st.tabs(["📊 Overview", "📝 Entry", "🎯 Goals", "📅 Schedule", "⚙️ Settings"])

# --- TAB 1: OVERVIEW ---
with tab1:
    if manual_due:
        st.write("### 🔔 Pending Manual Transfers")
        for task in manual_due:
            with st.expander(f"Due: {task['next_run_date']} - {task['description']} (${task['amount']})", expanded=True):
                st.write(f"**Action:** {task['type']} of ${task['amount']}")
                if st.button("✅ I have done this transfer", key=f"done_{task['id']}"):
                    cat = task.get('category', 'Recurring')
                    add_transaction(date.today(), task['amount'], f"✅ {task['description']}", 
                                  task['type'], task['from_account_id'], task['to_account_id'], cat, "Manual Scheduled")
                    
                    if task['frequency'] == 'Monthly':
                        next_date = datetime.strptime(task['next_run_date'], '%Y-%m-%d').date() + relativedelta(months=1)
                        supabase.table('schedule').update({"next_run_date": str(next_date)}).eq("id", task['id']).execute()
                    else:
                        supabase.table('schedule').delete().eq("id", task['id']).execute()
                    st.success("Recorded!")
                    st.rerun()
        st.divider()

    st.subheader("Current Finance Stand")
    nw_accounts = df_active[df_active['include_net_worth'] == True]
    net_worth = nw_accounts['balance'].sum() if not nw_accounts.empty else 0
    asset_accounts = df_active[df_active['is_liquid_asset'] == True]
    total_liquid = asset_accounts['balance'].sum() if not asset_accounts.empty else 0
    
    c1, c2 = st.columns(2)
    c1.metric("Net Worth", f"${net_worth:,.2f}") 
    c2.metric("Liquid Bank Assets", f"${total_liquid:,.2f}")

    st.divider()
    
    # ACCOUNT BREAKDOWN WITH REMARKS
    with st.expander("📂 View Account Breakdown & Notes"):
        st.dataframe(df_active[['name', 'balance', 'type', 'remark']], hide_index=True, use_container_width=True)

    st.divider()
    
    # CATEGORY CHART
    st.subheader("📊 Spending by Category")
    col_d1, col_d2 = st.columns(2)
    start_date = col_d1.date_input("Start Date", date.today().replace(day=1)) 
    end_date = col_d2.date_input("End Date", date.today())
    
    expenses = supabase.table('transactions').select("*") \
        .eq('type', 'Expense') \
        .gte('date', str(start_date)) \
        .lte('date', str(end_date)) \
        .order('date', desc=True).execute().data
        
    if expenses:
        df_exp = pd.DataFrame(expenses)
        if 'category' in df_exp.columns and not df_exp.empty:
            cat_sum = df_exp.groupby('category')['amount'].sum().reset_index().sort_values('amount', ascending=False)
            c_chart, c_list = st.columns([2, 1])
            with c_chart: st.bar_chart(cat_sum.set_index('category'))
            with c_list: st.dataframe(cat_sum, hide_index=True, use_container_width=True)
            
            st.write("---")
            st.write("**📂 Drill Down: Transaction Details**")
            sel_cat_view = st.selectbox("Select Category to View Details", cat_sum['category'].tolist())
            if sel_cat_view:
                df_detail = df_exp[df_exp['category'] == sel_cat_view]
                # Added 'remark' to the drill down view
                st.dataframe(df_detail[['date', 'description', 'remark', 'amount']], hide_index=True, use_container_width=True)
    else:
        st.info("No expenses found in this date range.")

    st.divider()
    
    # ACCOUNT HISTORY
    st.subheader("🔍 Account Details & History")
    selected_acc_name = st.selectbox("Select Account", account_list)
    if selected_acc_name:
        selected_acc_id = account_map[selected_acc_name]
        history = supabase.table('transactions').select("*") \
            .or_(f"from_account_id.eq.{selected_acc_id},to_account_id.eq.{selected_acc_id}") \
            .order('date', desc=True).limit(50).execute().data
            
        if history:
            # Added 'remark' to history table
            st.dataframe(pd.DataFrame(history)[['date', 'category', 'description', 'remark', 'amount', 'type', 'id']], hide_index=True, use_container_width=True)
            with st.expander("🗑️ Delete Transaction"):
                del_id = st.number_input("Transaction ID to Delete", min_value=0, step=1)
                if st.button("Delete Transaction"):
                    tx = supabase.table('transactions').select("*").eq('id', del_id).execute().data
                    if tx:
                        tx = tx[0]
                        if tx['type'] == "Expense": update_balance(tx['from_account_id'], tx['amount'])
                        elif tx['type'] in ["Income", "Refund"]: update_balance(tx['to_account_id'], -tx['amount'])
                        elif tx['type'] in ["Transfer", "Custodial In"]:
                            update_balance(tx['from_account_id'], tx['amount'])
                            update_balance(tx['to_account_id'], -tx['amount'])
                        elif tx['type'] == "Custodial Out":
                            if tx['from_account_id']: update_balance(tx['from_account_id'], tx['amount'])
                            update_balance(tx['to_account_id'], -tx['amount'])
                        supabase.table('transactions').delete().eq('id', del_id).execute()
                        st.success("Deleted!")
                        st.rerun()

# --- TAB 2: ENTRY ---
with tab2:
    st.subheader("New Transaction")
    t_type = st.radio("Type", ["Expense", "Income", "Transfer", "Custodial In", "Custodial Out"], horizontal=True)
    
    with st.form("entry"):
        c1, c2 = st.columns(2)
        date = c1.date_input("Date", datetime.today())
        
        # --- NEW: REMARK FIELD ---
        
        if t_type == "Custodial Out":
            st.info("Paying back custodial money (Split Payment)")
            cust_acc = st.selectbox("Which Custodial Account?", df_active[df_active['type']=='Custodial']['name']) if not df_active.empty else None
            
            st.write("--- Sources ---")
            col_bank, col_cash = st.columns(2)
            with col_bank:
                bank_source = st.selectbox("Bank Source", df_active[df_active['type'].isin(['Bank', 'Cash'])]['name'])
                bank_amount = st.number_input("Amount from Bank", min_value=0.0, format="%.2f")
            with col_cash:
                cash_source_name = st.selectbox("Cash Source", ["Physical Wallet (Untracked)"] + account_list)
                cash_amount = st.number_input("Amount from Cash", min_value=0.0, format="%.2f")
            
            desc = st.text_input("Description (Short)")
            remark = st.text_area("Remark / Notes (Optional)", height=68) # NEW FIELD
            category = "Custodial" 
            
            if st.form_submit_button("Process Split Payment"):
                if bank_amount > 0:
                    add_transaction(date, bank_amount, f"{desc} (Bank)", "Custodial Out", account_map[bank_source], account_map[cust_acc], category, remark)
                if cash_amount > 0:
                    cash_id = account_map.get(cash_source_name)
                    if not cash_id:
                        supabase.table('transactions').insert({
                            "date": str(date), "amount": cash_amount, "description": f"{desc} (Cash)", "type": "Custodial Out",
                            "to_account_id": account_map[cust_acc], "category": category, "remark": remark
                        }).execute()
                        update_balance(account_map[cust_acc], cash_amount)
                    else:
                        add_transaction(date, cash_amount, f"{desc} (Cash)", "Custodial Out", cash_id, account_map[cust_acc], category, remark)
                st.success("Saved!")
                st.rerun()

        else:
            amt = c2.number_input("Amount", min_value=0.01)
            cat_options = get_categories("Income") if t_type == "Income" else get_categories("Expense")
            category = st.selectbox("Category", cat_options)
            
            f_acc, t_acc = None, None
            if t_type == "Expense": f_acc = st.selectbox("Paid From", non_loan_accounts)
            elif t_type in ["Income", "Refund"]: t_acc = st.selectbox("Deposit To", account_list)
            elif t_type == "Transfer":
                c_a, c_b = st.columns(2)
                f_acc = c_a.selectbox("From", non_loan_accounts)
                t_acc = c_b.selectbox("To", account_list)
            elif t_type == "Custodial In":
                c_a, c_b = st.columns(2)
                f_acc = c_a.selectbox("Custodial Source", df_active[df_active['type']=='Custodial']['name'])
                t_acc = c_b.selectbox("Bank Received", df_active[df_active['type']=='Bank']['name'])

            desc = st.text_input("Description (Short)")
            remark = st.text_area("Remark / Notes (Optional)", height=68) # NEW FIELD
            
            if st.form_submit_button("Submit"):
                add_transaction(date, amt, desc, t_type, account_map.get(f_acc), account_map.get(t_acc), category, remark)
                st.success("Saved!")
                st.rerun()

# --- TAB 3: GOALS ---
with tab3:
    st.subheader("🎯 Sinking Funds Dashboard")
    goals = df_active[df_active['type'] == 'Sinking Fund']
    
    if not goals.empty:
        total_saved = goals['balance'].sum()
        total_goal = goals['goal_amount'].sum()
        
        if total_goal > 0: grand_progress = min(total_saved / total_goal, 1.0)
        else: grand_progress = 0.0
            
        st.write("### 🏆 Total Progress")
        c_gt1, c_gt2 = st.columns([1, 3])
        c_gt1.metric("Total Saved", f"${total_saved:,.2f}", f"Target: ${total_goal:,.2f}")
        c_gt2.write("")
        c_gt2.write("Overall Completion:")
        c_gt2.progress(grand_progress)
        
        st.divider()

        st.write("### Individual Funds")
        for index, row in goals.iterrows():
            with st.expander(f"📌 {row['name']} (Current: ${row['balance']:,.2f})", expanded=True):
                goal_amt = row['goal_amount'] or 0
                if row['balance'] >= goal_amt and goal_amt > 0:
                    st.success("🎉 GOAL ACHIEVED!")
                    with st.form(f"rotate_goal_{row['id']}"):
                        new_goal = st.number_input("New Goal Amount", value=float(goal_amt))
                        new_date = st.date_input("New Deadline")
                        if st.form_submit_button("🔄 Rotate Goal"):
                            update_account_settings(row['id'], row['name'], row['balance'], row['include_net_worth'], row['is_liquid_asset'], new_goal, new_date, row.get('sort_order', 99), row.get('is_active', True), row.get('remark', ''))
                            st.rerun()
                elif goal_amt > 0:
                    shortfall = goal_amt - row['balance']
                    progress = min(row['balance'] / goal_amt, 1.0)
                    st.progress(progress)
                    st.caption(f"Target: ${goal_amt:,.2f}")
                    if row['goal_date']:
                        deadline = datetime.strptime(row['goal_date'], '%Y-%m-%d').date()
                        months_left = (deadline.year - date.today().year) * 12 + (deadline.month - date.today().month)
                        if months_left > 0:
                            st.info(f"💡 Save **${shortfall / months_left:,.2f} / month**")
    else:
        st.info("No Sinking Funds created yet.")

# --- TAB 4: SCHEDULE ---
with tab4:
    st.subheader("Manage Future Payments")
    
    with st.expander("➕ Add New Schedule", expanded=True):
        with st.form("sch_form"):
            s_desc = st.text_input("Description (e.g. Rent, Investment)")
            c1, c2, c3 = st.columns(3)
            s_amount = c1.number_input("Amount", min_value=0.01)
            s_freq = c2.selectbox("Frequency", ["Monthly", "One-Time"])
            s_date = c3.date_input("Start Date", datetime.today())
            
            s_cat = st.selectbox("Category", get_categories())
            s_manual = st.checkbox("🔔 Manual Reminder? (Tick if you do the transfer manually)", value=False)
            s_type = st.selectbox("Type", ["Expense", "Transfer", "Income"])
            
            s_from, s_to = None, None
            if s_type == "Expense": s_from = st.selectbox("From Account", non_loan_accounts)
            elif s_type == "Income": s_to = st.selectbox("To Account", account_list)
            elif s_type == "Transfer":
                s_from = st.selectbox("From", non_loan_accounts, key="s_f")
                s_to = st.selectbox("To", account_list, key="s_t")
                
            if st.form_submit_button("Schedule It"):
                f_id = account_map.get(s_from)
                t_id = account_map.get(s_to)
                supabase.table('schedule').insert({
                    "description": s_desc, "amount": s_amount, "type": s_type, 
                    "from_account_id": f_id, "to_account_id": t_id, 
                    "frequency": s_freq, "next_run_date": str(s_date),
                    "is_manual": s_manual, "category": s_cat
                }).execute()
                st.success("Scheduled!")
                st.rerun()

    st.divider()
    upcoming = supabase.table('schedule').select("*").order('next_run_date').execute().data
    if upcoming:
        st.write("### 🗓️ Upcoming Items")
        df_up = pd.DataFrame(upcoming)
        df_up['Manual?'] = df_up['is_manual'].apply(lambda x: "🔔 Manual" if x else "🤖 Auto")
        if 'category' not in df_up.columns: df_up['category'] = "-"
        st.dataframe(df_up[['next_run_date', 'description', 'category', 'amount', 'frequency', 'Manual?', 'id']], hide_index=True)
        
        del_sch_id = st.number_input("Schedule ID to Delete", min_value=0)
        if st.button("Delete Schedule Item"):
            supabase.table('schedule').delete().eq("id", del_sch_id).execute()
            st.rerun()

# --- TAB 5: SETTINGS ---
with tab5:
    st.subheader("🔧 Configuration")
    
    with st.expander("📂 Manage Categories", expanded=False):
        c1, c2 = st.columns(2)
        with c1:
            st.write("**Add Category**")
            new_cat = st.text_input("Name", key="new_cat_name")
            new_cat_type = st.selectbox("Type", ["Expense", "Income"], key="new_cat_type")
            if st.button("Add"):
                supabase.table('categories').insert({"name": new_cat, "type": new_cat_type}).execute()
                st.success(f"Added {new_cat}")
                st.rerun()
        with c2:
            st.write("**Delete Category**")
            all_cats = get_categories()
            if all_cats:
                del_cat = st.selectbox("Select to Delete", all_cats)
                if st.button("Delete"):
                    supabase.table('categories').delete().eq("name", del_cat).execute()
                    st.rerun()

    st.divider()

    with st.expander("➕ Add New Account", expanded=False):
        with st.form("create_acc"):
            new_name = st.text_input("Name")
            new_type = st.selectbox("Type", ["Bank", "Credit Card", "Custodial", "Sinking Fund", "Cash", "Loan", "Investment"])
            
            bal_label = "Starting Balance"
            if new_type == "Sinking Fund": bal_label = "Existing Saved Amount (Import from Excel)"
            elif new_type == "Loan": bal_label = "Current Loan Amount (Negative Value)"
            
            initial_bal = st.number_input(bal_label, value=0.0)
            
            # REMARK FIELD FOR ACCOUNT
            new_remark = st.text_area("Account Notes (e.g., Min balance, Interest rate)", height=68)
            
            st.write("--- Goal Settings (Sinking Funds Only) ---")
            new_goal = st.number_input("Goal Amount", value=0.0)
            new_date = st.date_input("Goal Deadline", value=None)
            
            if st.form_submit_button("Create Account"):
                data = {
                    "name": new_name, "type": new_type, "balance": initial_bal,
                    "include_net_worth": True, "is_liquid_asset": True if new_type in ['Bank', 'Cash', 'Investment'] else False,
                    "goal_amount": new_goal if new_type == "Sinking Fund" else 0,
                    "goal_date": str(new_date) if new_type == "Sinking Fund" else None,
                    "sort_order": 99,
                    "is_active": True,
                    "remark": new_remark
                }
                supabase.table('accounts').insert(data).execute()
                st.success(f"Created {new_name}!")
                st.rerun()

    st.divider()
    
    # EDIT ACCOUNT
    df_all_accounts = get_accounts(show_inactive=True)
    edit_list = df_all_accounts['name'].tolist() if not df_all_accounts.empty else []
    
    edit_acc = st.selectbox("Edit Existing Account", edit_list)
    if edit_acc:
        row = df_all_accounts[df_all_accounts['name'] == edit_acc].iloc[0]
        
        with st.form("edit_settings"):
            st.write(f"Editing: **{row['name']}**")
            
            c_name, c_sort = st.columns([3, 1])
            upd_name = c_name.text_input("Account Name", value=row['name'])
            upd_sort = c_sort.number_input("Sort Order", value=int(row.get('sort_order', 99)), step=1)
            
            upd_bal = st.number_input("Current Balance (Manual Adjustment)", value=float(row['balance']))
            
            # REMARK EDITING
            upd_remark = st.text_area("Account Notes", value=row.get('remark', ''))

            c1, c2, c3 = st.columns(3)
            inc_nw = c1.checkbox("Include in Net Worth?", value=row['include_net_worth'])
            is_liq = c2.checkbox("Is Actual Bank Asset?", value=row['is_liquid_asset'])
            is_active = c3.checkbox("Account is Active?", value=row.get('is_active', True))
            
            g_amt = 0.0
            g_date = None
            if row['type'] == 'Sinking Fund':
                st.divider()
                st.write("Goal Settings")
                g_amt = st.number_input("Goal Amount", value=float(row['goal_amount'] or 0))
                g_date = st.date_input("Goal Deadline", value=datetime.strptime(row['goal_date'], '%Y-%m-%d') if row['goal_date'] else None)
            
            if st.form_submit_button("Save Changes"):
                update_account_settings(row['id'], upd_name, upd_bal, inc_nw, is_liq, g_amt, g_date, upd_sort, is_active, upd_remark)
                st.success("Updated! Refreshing...")
                st.rerun()
