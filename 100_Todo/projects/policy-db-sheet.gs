// =====================================================
// 保單資料庫審核表生成器
// 使用方式：
// 1. 開啟 Google Sheets（新增空白試算表）
// 2. 點選「擴充功能」→「Apps Script」
// 3. 貼入全部程式碼 → 點「執行」→「createPolicyReviewSheet」
// =====================================================

function createPolicyReviewSheet() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();

  // ── 建立三個分頁 ──
  const sheet1 = getOrCreateSheet(ss, '給付項目審核');
  const sheet2 = getOrCreateSheet(ss, '除外責任與限制');
  const sheet3 = getOrCreateSheet(ss, '理賠必要文件');

  buildCoverageSheet(sheet1);
  buildRestrictionsSheet(sheet2);
  buildClaimDocsSheet(sheet3);

  // 預設顯示第一頁
  sheet1.activate();
  SpreadsheetApp.getUi().alert('✅ 保單審核表建立完成！\n\n顧問請在「狀態」欄選擇審核結果。');
}

// ─────────────────────────────────────────
// 工具：取得或新建分頁
// ─────────────────────────────────────────
function getOrCreateSheet(ss, name) {
  let sheet = ss.getSheetByName(name);
  if (sheet) { sheet.clearContents(); sheet.clearFormats(); }
  else { sheet = ss.insertSheet(name); }
  return sheet;
}

// ─────────────────────────────────────────
// 分頁 1：給付項目審核表
// ─────────────────────────────────────────
function buildCoverageSheet(sheet) {
  // ── 標題區塊 ──
  sheet.getRange('A1').setValue('【定額醫療】南山人壽　｜　南山人壽終身醫療保險');
  sheet.getRange('A1:F1').merge();
  styleHeader(sheet.getRange('A1:F1'), '#1a1a2e', '#ffffff', 14, true);

  sheet.getRange('A2').setValue('保額基礎：日額（C型回推）　｜　狀態：待審核　｜　提取日期：2026-06-01');
  sheet.getRange('A2:F2').merge();
  styleHeader(sheet.getRange('A2:F2'), '#16213e', '#e0e0e0', 10, false);

  // ── 欄位標題 ──
  const headers = ['給付項目', '公式', '限制條件', '注意事項', '✅ 狀態', '📝 審核備注'];
  sheet.getRange('A4:F4').setValues([headers]);
  styleHeader(sheet.getRange('A4:F4'), '#0f3460', '#ffffff', 11, true);

  // ── 給付項目資料 ──
  const items = [
    ['住院日額保險金',             '日額×1/日',    '—',            '31日起×2',                     '待審核', ''],
    ['加護病房暨燒燙傷中心',       '日額×3/日',    '—',            '住院日額×1＋另給付×2',           '待審核', ''],
    ['重大手術醫療保險金',         '日額×50/次',   '同一住院限1次', '附表一共13類重大手術',           '待審核', ''],
    ['重大手術暨重大疾病特別看護', '日額×1/日',    '最高28日/次',  '重大手術或7大重大疾病擇一觸發',   '待審核', ''],
    ['住院前後門診醫療保險金',     '日額×0.25/次', '每日1次',      '住院前後2週，同一疾病/傷害',      '待審核', ''],
    ['出院療養保險金',             '日額×0.5/日',  '—',            '依實際住院日數一次給付',          '待審核', ''],
    ['緊急醫療運送保險金',         '日額×2/次',    '同一住院限1次', '需以救護車緊急運送',              '待審核', ''],
    ['身故保險金',                 '日額×1000',    '扣除已領各項合計', '給付後契約終止',              '待審核', ''],
  ];

  const dataRange = sheet.getRange(5, 1, items.length, 6);
  dataRange.setValues(items);
  dataRange.setFontSize(10);
  dataRange.setVerticalAlignment('middle');

  // 斑馬紋
  for (let i = 0; i < items.length; i++) {
    const rowRange = sheet.getRange(5 + i, 1, 1, 6);
    rowRange.setBackground(i % 2 === 0 ? '#f8f9fa' : '#ffffff');
  }

  // ── 累積上限（特別標注）──
  const limitRow = items.length + 5 + 1;
  sheet.getRange(limitRow, 1, 1, 6).setValues([
    ['⚠️ 累積給付上限', '日額×1000', '所有給付項目合計', '達到後契約自動終止', '待審核', '']
  ]);
  styleHeader(sheet.getRange(limitRow, 1, 1, 6), '#e8c547', '#1a1a2e', 10, true);

  // ── 狀態下拉選單 ──
  const statusRange = sheet.getRange(5, 5, items.length + 1, 1);
  const rule = SpreadsheetApp.newDataValidation()
    .requireValueInList(['待審核', '已審核', '有疑問'], true)
    .setAllowInvalid(false)
    .build();
  statusRange.setDataValidation(rule);

  // ── 條件格式：狀態顏色 ──
  applyStatusFormatting(sheet, 5, items.length + 2);

  // ── 欄寬設定 ──
  sheet.setColumnWidth(1, 220);  // 給付項目
  sheet.setColumnWidth(2, 110);  // 公式
  sheet.setColumnWidth(3, 130);  // 限制條件
  sheet.setColumnWidth(4, 210);  // 注意事項
  sheet.setColumnWidth(5, 90);   // 狀態
  sheet.setColumnWidth(6, 200);  // 審核備注

  // ── 行高 ──
  sheet.setRowHeight(1, 40);
  sheet.setRowHeight(2, 28);
  sheet.setRowHeight(4, 30);
  for (let i = 5; i <= limitRow; i++) sheet.setRowHeight(i, 28);

  // ── 框線 ──
  sheet.getRange(4, 1, items.length + 2, 6)
    .setBorder(true, true, true, true, true, true, '#cccccc', SpreadsheetApp.BorderStyle.SOLID);

  // ── 凍結標題 ──
  sheet.setFrozenRows(4);
}

// ─────────────────────────────────────────
// 分頁 2：除外責任與限制
// ─────────────────────────────────────────
function buildRestrictionsSheet(sheet) {
  sheet.getRange('A1').setValue('理賠條件與限制　｜　南山人壽終身醫療保險');
  sheet.getRange('A1:C1').merge();
  styleHeader(sheet.getRange('A1:C1'), '#1a1a2e', '#ffffff', 13, true);

  // 等待期
  const waitData = [
    ['⏳ 等待期', '類型', '天數'],
    ['', '疾病', '30天'],
    ['', '傷害意外', '無'],
    ['', '增加單位日額部分', '另計30天'],
  ];
  sheet.getRange(3, 1, waitData.length, 3).setValues(waitData);
  styleHeader(sheet.getRange('A3:C3'), '#0f3460', '#ffffff', 10, true);
  sheet.getRange(4, 1, 3, 3).setBackground('#f0f4ff');

  // 除外責任
  const exRow = 9;
  sheet.getRange(exRow, 1).setValue('🚫 除外責任');
  sheet.getRange(exRow, 1, 1, 3).merge();
  styleHeader(sheet.getRange(exRow, 1, 1, 3), '#c0392b', '#ffffff', 10, true);

  const exclusions = [
    ['✗', '故意行為（含自殺/自殺未遂）'],
    ['✗', '犯罪行為'],
    ['✗', '非法施用防制毒品'],
    ['✗', '飲酒後駕車（酒測超標）'],
    ['✗', '美容手術、外科整型（重建基本功能者不在此限）'],
    ['✗', '外觀可見之天生畸形'],
    ['✗', '健康檢查、療養、靜養、戒毒、戒酒、護理或養老'],
    ['✗', '懷孕、流產或分娩（醫療必要之特定情形除外）'],
    ['✗', '不孕症、人工受孕或非以治療為目的之避孕及絕育手術'],
  ];
  sheet.getRange(exRow + 1, 1, exclusions.length, 2).setValues(exclusions);
  sheet.getRange(exRow + 1, 1, exclusions.length, 2).setFontColor('#c0392b');

  // 特別注意
  const noteRow = exRow + exclusions.length + 2;
  sheet.getRange(noteRow, 1).setValue('⚠️ 特別注意');
  sheet.getRange(noteRow, 1, 1, 3).merge();
  styleHeader(sheet.getRange(noteRow, 1, 1, 3), '#e67e22', '#ffffff', 10, true);

  const notes = [
    ['✗', '累積給付上限日額×1000，達到後契約自動終止'],
    ['✗', '同一疾病出院14日內再住同院，視為同一次住院'],
    ['✗', '特別看護同一住院最高28日；重大手術+重大疾病同觸發僅給付一次'],
    ['✗', '身故給付須扣除第六至十二條累計已領各項合計'],
    ['✗', '重大疾病保障需滿30天等待期（傷害致器官移植除外）'],
  ];
  sheet.getRange(noteRow + 1, 1, notes.length, 2).setValues(notes);
  sheet.getRange(noteRow + 1, 1, notes.length, 2).setFontColor('#e67e22');

  sheet.setColumnWidth(1, 40);
  sheet.setColumnWidth(2, 420);
  sheet.setColumnWidth(3, 100);
  sheet.setFrozenRows(1);
}

// ─────────────────────────────────────────
// 分頁 3：理賠必要文件
// ─────────────────────────────────────────
function buildClaimDocsSheet(sheet) {
  sheet.getRange('A1').setValue('理賠必要文件　｜　南山人壽終身醫療保險');
  sheet.getRange('A1:D1').merge();
  styleHeader(sheet.getRange('A1:D1'), '#1a1a2e', '#ffffff', 13, true);

  const sections = [
    {
      title: '🏥 一般住院／加護病房', col: 1,
      docs: ['保險金申請書', '被保險人身分證影本', '醫療診斷書（含住院原因/入出院日期）', '加護病房住院證明（載明起訖日期）', '受益人身分證影本', '受益人存摺封面影本']
    },
    {
      title: '🔪 重大手術', col: 2,
      docs: ['保險金申請書', '被保險人身分證影本', '醫療診斷書（載明手術名稱及日期）', '手術紀錄書', '開刀房紀錄（含麻醉方式）', '受益人身分證影本', '受益人存摺封面影本']
    },
    {
      title: '🚪 住院前後門診', col: 3,
      docs: ['保險金申請書', '門診收據（含就診日期/診斷）', '住院診斷書（證明同一疾病/傷害）', '受益人身分證影本', '受益人存摺封面影本']
    },
    {
      title: '💊 出院療養', col: 4,
      docs: ['保險金申請書', '出院診斷書（含出院日期及住院日數）', '受益人身分證影本', '受益人存摺封面影本']
    },
  ];

  const sectionColors = ['#1a6b4a', '#0f3460', '#6b3a1f', '#4a1a6b'];

  sections.forEach((sec, idx) => {
    const col = sec.col;
    sheet.getRange(3, col).setValue(sec.title);
    styleHeader(sheet.getRange(3, col), sectionColors[idx], '#ffffff', 10, true);
    sec.docs.forEach((doc, i) => {
      sheet.getRange(4 + i, col).setValue('· ' + doc);
      sheet.getRange(4 + i, col).setBackground(i % 2 === 0 ? '#f8f9fa' : '#ffffff');
    });
    sheet.setColumnWidth(col, 230);
  });

  // 身故/緊急運送/重大疾病 (第二排)
  const sections2 = [
    {
      title: '🕊️ 身故／喪葬費用', col: 1,
      docs: ['保險金申請書', '保險單或其謄本', '死亡診斷書（或相驗屍體證明書）', '除戶戶籍謄本', '受益人戶籍謄本', '受益人身分證影本', '受益人存摺封面影本']
    },
    {
      title: '🚑 緊急醫療運送', col: 2,
      docs: ['保險金申請書', '救護車費用收據', '急診或住院證明', '受益人身分證影本', '受益人存摺封面影本']
    },
    {
      title: '💔 重大疾病確診（特別看護觸發）', col: 3,
      docs: ['診斷書（載明重大疾病種類）', '心肌梗塞：心電圖＋心肌酶報告', '冠狀動脈繞道：心導管報告＋手術紀錄', '腦中風：腦部影像（CT/MRI）', '慢性腎衰竭：腎功能檢查＋洗腎紀錄', '癌症：病理組織切片報告', '癱瘓：神經科專科醫師診斷書', '器官移植：移植手術紀錄']
    },
  ];
  const startRow2 = 14;
  const sectionColors2 = ['#7d1f1f', '#1f4d7d', '#5a1f7d'];
  sections2.forEach((sec, idx) => {
    const col = sec.col;
    sheet.getRange(startRow2, col).setValue(sec.title);
    styleHeader(sheet.getRange(startRow2, col), sectionColors2[idx], '#ffffff', 10, true);
    sec.docs.forEach((doc, i) => {
      sheet.getRange(startRow2 + 1 + i, col).setValue('· ' + doc);
      sheet.getRange(startRow2 + 1 + i, col).setBackground(i % 2 === 0 ? '#f8f9fa' : '#ffffff');
    });
  });

  sheet.setFrozenRows(1);
}

// ─────────────────────────────────────────
// 樣式工具函數
// ─────────────────────────────────────────
function styleHeader(range, bgColor, fontColor, fontSize, bold) {
  range.setBackground(bgColor)
       .setFontColor(fontColor)
       .setFontSize(fontSize)
       .setFontWeight(bold ? 'bold' : 'normal')
       .setVerticalAlignment('middle')
       .setHorizontalAlignment('left')
       .setWrap(true);
}

function applyStatusFormatting(sheet, startRow, numRows) {
  const colors = {
    '已審核': { bg: '#d4edda', font: '#155724' },
    '有疑問': { bg: '#f8d7da', font: '#721c24' },
    '待審核': { bg: '#fff3cd', font: '#856404' },
  };

  Object.entries(colors).forEach(([status, style]) => {
    const rule = SpreadsheetApp.newConditionalFormatRule()
      .whenTextEqualTo(status)
      .setBackground(style.bg)
      .setFontColor(style.font)
      .setRanges([sheet.getRange(startRow, 5, numRows, 1)])
      .build();
    const rules = sheet.getConditionalFormatRules();
    rules.push(rule);
    sheet.setConditionalFormatRules(rules);
  });
}
