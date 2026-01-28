const fs = require('fs');
const path = require('path');

const cachePath = path.join(__dirname, 'video_cache.json');
const outputPath = path.join(__dirname, 'citizen-issues.json');

// Default values
const userId = '6763b8d071d82d13f7bd495d';
const companyId = '66fbfa9e301e2401c4d109c7';
const now = new Date().toISOString().split('T')[0] + 'T00:00:00.000Z';

const cache = JSON.parse(fs.readFileSync(cachePath, 'utf8'));
const videos = cache.videos;

const issues = videos.map(video => ({
  title: video.title,
  description: video.description,
  issueType:
    video.type === 'video' ? 'News' :
    video.type === 'short' ? 'Post' :
    'Post',
  video: `https://www.youtube.com/embed/${video.id}`,
  status: 'New',
  userId,
  companyId,
  issueDate: video.upload_date ? video.upload_date + 'T00:00:00.000Z' : now,
  created: now,
  modified: now
}));

fs.writeFileSync(outputPath, JSON.stringify(issues, null, 2), 'utf8');
console.log(`Written ${issues.length} issues to ${outputPath}`);
