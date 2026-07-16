<<<<<<< HEAD
# 📊 Indian Startup Funding Tracker — Excel Dashboard

An interactive **Microsoft Excel dashboard** that analyses India's startup‑funding landscape from **2019 to 2024**, built entirely with native Excel **PivotTables, PivotCharts & Slicers** — no add‑ins, no macros.

![Dashboard](screenshots/1-dashboard.png)

---

## 🔎 Overview

The dashboard turns a **700‑row funding dataset** into a single, interactive screen that answers key business questions at a glance:

- Which **sectors** and **cities** attract the most capital?
- How has **funding changed year over year**?
- How is capital distributed across **investment stages** (Seed → Series D+)?
- Which individual **startups** raised the largest rounds?

Every visual is fully interactive — a single click on a slicer filters **all charts and KPIs** at once.

---

## ✨ Key Features

| Feature | Detail |
|---|---|
| **5 KPI cards** | Total Funding, Total Deals, Avg Deal Size, Total Valuation, Largest Deal |
| **6 PivotCharts** | Sector, Stage (donut), Yearly trend, Cities, Deals-by-sector, Top‑10 startups |
| **4 Slicers** | Sector · Stage · Year · City — cross‑filter the whole dashboard |
| **Summary bar** | Funded startups, funding, employees, valuation, largest deal |
| **Design** | Modern dark theme, single‑screen layout, rounded KPI cards |

---

## 📈 Headline Metrics (full dataset)

| Metric | Value |
|---|---|
| Total Funding | **$31,623 M** (≈ $31.6 B) |
| Total Deals | **700** |
| Avg Deal Size | **$45.2 M** |
| Total Valuation | **$225.7 B** |
| Largest Deal | **$494.1 M** |
| Employees (across startups) | **610,612** |

---

## 🗂️ Dataset

A realistic, **simulated** dataset of **700 funding rounds** (2019–2024). Company names are fictional; structure and value ranges mirror real Indian startup‑funding data, so the analysis techniques are identical to those used on live data.

![Raw Data](screenshots/3-raw-data.png)

**Columns (11):** `Company` · `Sector` · `City` · `Funding Stage` · `Funding Amount ($M)` · `Funding Date` · `Funding Year` · `Lead Investor` · `Founded Year` · `Valuation ($M)` · `Employees`

---

## 🧮 Data Engine — PivotTables

All visuals are driven by PivotTables on a dedicated **Pivots** sheet (Sector, Stage, Year, City, deal counts, top companies, plus single‑value KPI pivots).

![Pivot Tables](screenshots/2-pivot-tables.png)

---

## 💡 Key Insights

- **Market size:** ~**$31.6 B** raised across 700 deals, averaging **$45.2 M** per deal.
- **Top sectors:** E‑commerce, Fintech and Healthtech lead on total capital.
- **Deal volume:** Fintech (102) and SaaS (98) record the most deals — high activity even where rounds are smaller.
- **Funding hubs:** Bengaluru and Delhi NCR together attract roughly **half** of all funding.
- **Stage mix:** Late‑stage rounds (Series C & D+) make up **~79%** of capital; Seed is only ~2% by value.
- **Yearly trend:** Funding peaked in **2020 (~$6.9 B)**, then settled in the **$4.4–5.1 B** range.

---

## 🛠️ Built With

`Microsoft Excel` · `PivotTables` · `PivotCharts` · `Slicers` · `Dashboard Design` · `Data Visualization`

**Skills demonstrated:** data modelling, aggregation with PivotTables, interactive reporting, visual design & storytelling.

---

## 📁 Repository Structure

```
excel/
├── Startup_Funding_Dashboard.xlsx        # The interactive dashboard (Dashboard · Pivots · Data)
├── Startup_Funding_Dashboard_Report.docx # Project report / documentation
├── screenshots/                          # Dashboard images used in this README
│   ├── 1-dashboard.png
│   ├── 2-pivot-tables.png
│   └── 3-raw-data.png
└── README.md
=======
# 🚀 Indian Startup Funding Tracker Dashboard (Excel)

An interactive Microsoft Excel dashboard that analyzes startup funding trends across India from **2019–2024**. The project transforms raw funding data into an insightful dashboard using Pivot Tables, Pivot Charts, KPI Cards, and Slicers for dynamic business analysis.

---

## 📌 Project Overview

This dashboard helps users explore the Indian startup ecosystem by answering questions such as:

- Which sectors receive the highest funding?
- Which cities attract the most investments?
- How has startup funding changed over time?
- Which funding stages dominate the market?
- Which startups raised the largest investments?

The dashboard is completely interactive and updates all charts instantly using slicers.

---

# 📷 Dashboard Preview

## Complete Dashboard

![Dashboard](screenshots/dashboard.png)

---

## KPI Cards

![KPI Cards](screenshots/kpi_cards.png)

---

## Funding by Sector

![Sector Analysis](screenshots/sector_analysis.png)

---

## Funding by City

![City Analysis](screenshots/city_analysis.png)

---

## Year-wise Funding Trend

![Yearly Trend](screenshots/yearly_trend.png)

---

## Top Funded Startups

![Top Startups](screenshots/top_startups.png)

---

# 📊 Dashboard Features

✔ Interactive KPI Cards

- Total Funding
- Total Deals
- Average Deal Size
- Total Valuation
- Largest Funding Round

✔ Interactive Charts

- Funding by Sector
- Funding by Stage
- Funding Trend by Year
- Top Cities by Funding
- Number of Deals by Sector
- Top 10 Funded Startups

✔ Dynamic Slicers

- Sector
- City
- Funding Stage
- Funding Year

---

# 🗂 Dataset Information

| Feature | Description |
|----------|-------------|
| Records | 700 Startup Funding Deals |
| Period | 2019–2024 |
| Cities | 9 Indian Startup Hubs |
| Sectors | 12 Industry Categories |
| Columns | 11 |

### Dataset Fields

- Company
- Sector
- City
- Funding Stage
- Funding Amount
- Funding Date
- Funding Year
- Lead Investor
- Founded Year
- Valuation
- Employees

> **Note:** The dataset is simulated for educational purposes and closely resembles real-world startup funding data.

---

# 🛠 Tools Used

- Microsoft Excel
- Pivot Tables
- Pivot Charts
- Slicers
- KPI Cards
- Excel Tables
- Conditional Formatting

---

# 📈 Key Insights

- 💰 Total Funding exceeded **$31 Billion**
- 📊 Around **700 funding deals** were analyzed
- 🏙 Bengaluru and Delhi NCR attracted the highest investments
- 💹 E-Commerce and FinTech dominated overall funding
- 🚀 Late-stage funding accounted for the majority of capital
- 📈 Funding peaked around 2020 before stabilizing

---

# 📂 Repository Structure

```
Startup-Funding-Dashboard/
│
├── Dashboard.xlsx
├── Project_Report.pdf
├── README.md
│
└── screenshots/
    ├── dashboard.png
    ├── kpi_cards.png
    ├── sector_analysis.png
    ├── city_analysis.png
    ├── yearly_trend.png
    └── top_startups.png
>>>>>>> ac1bc3d786872dc4e2df1e780237213a9894c21f
```

---

<<<<<<< HEAD
## ▶️ How to Use

1. Download and open **`Startup_Funding_Dashboard.xlsx`** in Microsoft Excel.
2. If a yellow bar appears, click **Enable Editing**.
3. The workbook opens on the **Dashboard** sheet.
4. Click any tile in a **slicer** (e.g. *Fintech*, *2023*) to filter every chart and KPI. Ctrl‑click to multi‑select; use a slicer's clear icon to reset.
5. *(If a chart looks blank on first open, press **Ctrl+Alt+F9** to recalculate.)*

---

## 👤 Author

**CHAITANYA-2002** — [GitHub](https://github.com/CHAITANYA-2002)

> 📝 *Dataset is simulated for an academic / portfolio project.*
=======
# ▶ How to Use

1. Download the Excel workbook.
2. Open it in Microsoft Excel.
3. Navigate to the Dashboard sheet.
4. Use the slicers to filter data.
5. All KPIs and charts update automatically.

---

# 🎯 Skills Demonstrated

- Data Cleaning
- Data Visualization
- Business Intelligence
- Dashboard Design
- Excel Pivot Tables
- Pivot Charts
- KPI Design
- Interactive Reporting
- Data Analysis

---

# 📌 Project Highlights

- Interactive Excel Dashboard
- Modern Dark Theme
- Fully Dynamic Visualizations
- Business-Oriented KPIs
- Professional Dashboard Layout
- Recruiter-Friendly Portfolio Project

---

## 👨‍💻 Author

**Chaitanya Khurana**

If you found this project useful, consider giving it a ⭐ on GitHub.
>>>>>>> ac1bc3d786872dc4e2df1e780237213a9894c21f
