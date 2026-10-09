/**
 * M1-6 方案 A 样例：@react-pdf/renderer（临时脚本，不入库）。
 *
 * 这是「方案 A」的核心证据：为了产出 PDF，必须**另写一套排版代码**。
 * 它跟 `apps/api/app/render/templates/resume_classic.html.j2` 描的是同一份
 * `resume_sample.json`，但两者没有任何共享 —— HTML/CSS 那一套在这里完全用不上，
 * 页面尺寸、行高、分页规则、字体全部要重新表达一遍。这份代码本身就是成本。
 *
 * 输入：../_m1_out/resume_sample.json
 * 输出：../_m1_out/sample_A_react_pdf.pdf
 *
 * 用法（在 scripts/_pdfproto 下）：
 *     node render.mjs
 */

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import React from "react";
import { Document, Font, Page, StyleSheet, Text, View, renderToFile } from "@react-pdf/renderer";

const here = path.dirname(fileURLToPath(import.meta.url));
const OUT = path.resolve(here, "..", "_m1_out");

// ---------------------------------------------------------------- 中文字体
// react-pdf 不走系统字体栈，必须显式喂字体文件，否则中文全是方框。
// 选 Deng.ttf / Dengb.ttf：单文件 TTF（msyh.ttc 是字体集合，fontkit 处理更麻烦）。
const FONTS = {
  regular: "C:/Windows/Fonts/Deng.ttf",
  bold: "C:/Windows/Fonts/Dengb.ttf",
};

for (const [role, file] of Object.entries(FONTS)) {
  if (!fs.existsSync(file)) {
    throw new Error(`缺少字体文件 ${file}（中文会渲染成方框）`);
  }
}
Font.register({
  family: "Deng",
  fonts: [
    { src: FONTS.regular, fontWeight: 400 },
    { src: FONTS.bold, fontWeight: 700 },
  ],
});

// ------------------------------------------------------- 中文断行（必须补的一课）
// react-pdf 的换行器按**空格**切词，中文没有空格 → 整段中文被当成一个「词」，
// 放不下就直接溢出页面边界（实测最右到 599pt，而页宽只有 595pt，文字已经跑出纸面）。
// 这一条在 HTML/CSS 那边完全不用操心 —— 浏览器自带 CJK 断行。
// 解决：注册 hyphenation callback，把含中文的「词」逐字拆开。
Font.registerHyphenationCallback((word) => {
  if (!/[\u2E80-\u9FFF\u3000-\u303F\uFF00-\uFFEF]/.test(word)) {
    return [word];
  }
  // 逐字拆；紧跟一个空串，避免 react-pdf 在断点处插入连字符
  return Array.from(word).flatMap((char) => [char, ""]);
});

// 单位是 pt；HTML 那边写的是 mm，这里要自己换算一遍 —— 又一处「两套代码」的实证
const MM = 2.8346456693;
const ACCENT = "#1f4e79";
const INK = "#1a1a1a";
const MUTED = "#5a6472";

const styles = StyleSheet.create({
  page: {
    fontFamily: "Deng",
    fontSize: 10.5,
    lineHeight: 1.55,
    color: INK,
    paddingTop: 14 * MM,
    paddingBottom: 14 * MM,
    paddingLeft: 16 * MM,
    paddingRight: 16 * MM,
  },
  head: { borderBottomWidth: 2, borderBottomColor: ACCENT, paddingBottom: 6 },
  name: { fontSize: 20, fontWeight: 700 },
  headline: { marginTop: 3, fontSize: 10, color: MUTED },

  section: { marginTop: 12 },
  sectionTitle: {
    fontSize: 11.5,
    color: ACCENT,
    fontWeight: 700,
    marginBottom: 6,
    paddingLeft: 7,
    borderLeftWidth: 3,
    borderLeftColor: ACCENT,
  },

  entry: { marginBottom: 9 },
  entryHead: { flexDirection: "row", justifyContent: "space-between", alignItems: "flex-end" },
  org: { fontWeight: 700 },
  role: { color: MUTED },
  period: { fontSize: 9.5, color: MUTED },

  bulletRow: { flexDirection: "row", marginTop: 2 },
  // flexBasis 必须显式给 0：react-pdf 的 Yoga 默认 flexShrink=0，
  // 只写 `flex: 1` 时文本盒依旧按内容宽度撑开，长要点会顶出右边界被裁掉。
  bulletMark: { width: 12, flexShrink: 0, color: ACCENT },
  bulletText: { flexGrow: 1, flexShrink: 1, flexBasis: 0 },
});

const h = React.createElement;

function Bullets({ bullets }) {
  return h(
    View,
    null,
    ...bullets.map((bullet, index) =>
      h(
        View,
        { style: styles.bulletRow, key: index, wrap: false },
        h(Text, { style: styles.bulletMark }, "•"),
        h(Text, { style: styles.bulletText }, bullet.text),
      ),
    ),
  );
}

function Entry({ entry }) {
  const title = [h(Text, { style: styles.org, key: "org" }, entry.org)];
  if (entry.role) {
    title.push(h(Text, { style: styles.role, key: "role" }, ` · ${entry.role}`));
  }
  return h(
    View,
    { style: styles.entry, wrap: false },
    h(
      View,
      { style: styles.entryHead },
      h(View, { style: { flexDirection: "row" } }, ...title),
      entry.period ? h(Text, { style: styles.period }, entry.period) : null,
    ),
    entry.bullets?.length ? h(Bullets, { bullets: entry.bullets }) : null,
  );
}

function ResumeDocument({ data }) {
  return h(
    Document,
    { title: data.title, author: data.header?.name ?? "" },
    h(
      Page,
      { size: "A4", style: styles.page },
      h(
        View,
        { style: styles.head },
        h(Text, { style: styles.name }, data.header?.name ?? ""),
        data.header?.headline ? h(Text, { style: styles.headline }, data.header.headline) : null,
        h(Text, { style: styles.headline }, `目标岗位：${data.title}`),
      ),
      ...(data.sections ?? []).map((section, index) =>
        h(
          View,
          { style: styles.section, key: index, minPresenceAhead: 40 },
          h(Text, { style: styles.sectionTitle }, section.title),
          ...section.entries.map((entry, entryIndex) =>
            h(Entry, { entry, key: entryIndex }),
          ),
        ),
      ),
    ),
  );
}

const inputPath = path.join(OUT, "resume_sample.json");
const data = JSON.parse(fs.readFileSync(inputPath, "utf8"));
const target = path.join(OUT, "sample_A_react_pdf.pdf");

await renderToFile(h(ResumeDocument, { data }), target);

const bytes = fs.statSync(target).size;
console.log(
  JSON.stringify({
    ok: true,
    file: path.basename(target),
    bytes,
    sections: data.sections?.length ?? 0,
    bullets: (data.sections ?? []).reduce(
      (sum, section) => sum + section.entries.reduce((n, e) => n + (e.bullets?.length ?? 0), 0),
      0,
    ),
  }),
);
