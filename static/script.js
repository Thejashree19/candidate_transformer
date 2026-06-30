document.getElementById('extractForm').addEventListener('submit', async (e) => {
    e.preventDefault();
    
    const form = e.target;
    const formData = new FormData();
    
    // Append files
    ['csv', 'ats', 'notes'].forEach(id => {
        const fileInput = document.getElementById(id);
        if (fileInput.files.length > 0) {
            formData.append(id, fileInput.files[0]);
        }
    });

    const resumesInput = document.getElementById('resumes');
    if (resumesInput.files.length > 0) {
        for (let i = 0; i < resumesInput.files.length; i++) {
            formData.append('resumes', resumesInput.files[i]);
        }
    }

    // Append URLs
    ['github', 'linkedin'].forEach(id => {
        const val = document.getElementById(id).value;
        if (val) {
            formData.append(id, val);
        }
    });

    // Loading State
    const submitBtn = document.getElementById('submitBtn');
    const originalBtnHTML = submitBtn.innerHTML;
    submitBtn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Processing...';
    submitBtn.disabled = true;

    const resultsContainer = document.getElementById('resultsContainer');
    resultsContainer.innerHTML = `
        <div class="empty-state">
            <div class="spinner"></div>
            <h2>Fusing Candidates...</h2>
            <p>Running multi-source data through the transformation engine.</p>
        </div>
    `;

    try {
        const response = await fetch('/api/extract', {
            method: 'POST',
            body: formData
        });

        if (!response.ok) {
            throw new Error(`Server error: ${response.statusText}`);
        }

        const data = await response.json();
        renderResults(data);
    } catch (error) {
        resultsContainer.innerHTML = `
            <div class="empty-state">
                <i class="fa-solid fa-triangle-exclamation" style="color: var(--danger)"></i>
                <h2>Extraction Failed</h2>
                <p>${error.message}</p>
            </div>
        `;
    } finally {
        submitBtn.innerHTML = originalBtnHTML;
        submitBtn.disabled = false;
    }
});

function renderResults(data) {
    const resultsContainer = document.getElementById('resultsContainer');
    const totalProfilesEl = document.getElementById('totalProfiles');
    
    totalProfilesEl.textContent = data.metadata.total_profiles;
    resultsContainer.innerHTML = '';

    if (data.profiles.length === 0) {
        resultsContainer.innerHTML = `
            <div class="empty-state">
                <i class="fa-solid fa-ghost"></i>
                <h2>No Profiles Extracted</h2>
                <p>Check the console or your inputs.</p>
            </div>
        `;
        return;
    }

    const template = document.getElementById('profileCardTemplate');

    data.profiles.forEach((profile, index) => {
        const clone = template.content.cloneNode(true);
        const card = clone.querySelector('.profile-card');
        card.style.animationDelay = `${index * 0.1}s`;

        // Header
        clone.querySelector('.candidate-name').textContent = profile.full_name || 'Unknown Candidate';
        clone.querySelector('.initials').textContent = getInitials(profile.full_name);
        
        let headline = profile.headline;
        if (!headline && profile.current_title && profile.current_company) {
            headline = `${profile.current_title} at ${profile.current_company}`;
        }
        clone.querySelector('.candidate-headline').textContent = headline || 'No headline available';
        
        clone.querySelector('.confidence-badge').innerHTML = 
            `<i class="fa-solid fa-check-circle"></i> ${(profile.overall_confidence * 100).toFixed(0)}% Confidence`;

        // Body info
        clone.querySelector('.emails-list').textContent = profile.emails.length > 0 ? profile.emails.join(', ') : 'No email';
        clone.querySelector('.phones-list').textContent = profile.phones.length > 0 ? profile.phones.join(', ') : 'No phone';
        
        const loc = profile.location;
        const locStr = [loc?.city, loc?.region, loc?.country].filter(Boolean).join(', ');
        clone.querySelector('.location-text').textContent = locStr || 'Unknown location';
        
        clone.querySelector('.experience-years').textContent = 
            profile.years_experience !== null ? `${profile.years_experience} years experience` : 'Experience unknown';

        // Skills
        const skillsContainer = clone.querySelector('.skills-container');
        if (profile.skills.length > 0) {
            profile.skills.sort((a, b) => b.confidence - a.confidence).forEach(skill => {
                const tag = document.createElement('div');
                tag.className = 'skill-tag';
                tag.innerHTML = `
                    ${skill.name}
                    <div class="skill-confidence-bar">
                        <div class="skill-confidence-fill" style="width: ${skill.confidence * 100}%"></div>
                    </div>
                `;
                skillsContainer.appendChild(tag);
            });
        } else {
            skillsContainer.innerHTML = '<span style="color: var(--text-muted)">No skills extracted</span>';
        }

        // Experience
        const expContainer = clone.querySelector('.experience-container');
        if (profile.experience.length > 0) {
            profile.experience.forEach(exp => {
                const div = document.createElement('div');
                div.className = 'timeline-item';
                div.innerHTML = `
                    <h4>${exp.title}</h4>
                    <div class="sub-text">${exp.company} • ${exp.start || '?'} - ${exp.end || 'Present'}</div>
                    ${exp.summary ? `<p>${exp.summary}</p>` : ''}
                `;
                expContainer.appendChild(div);
            });
        } else {
            expContainer.innerHTML = '<span style="color: var(--text-muted)">No experience history</span>';
        }

        // Education
        const eduContainer = clone.querySelector('.education-container');
        if (profile.education.length > 0) {
            profile.education.forEach(edu => {
                const div = document.createElement('div');
                div.className = 'timeline-item';
                div.innerHTML = `
                    <h4>${edu.institution || 'Unknown Institution'}</h4>
                    <div class="sub-text">${edu.degree || ''} ${edu.field || ''} ${edu.end_year ? `• ${edu.end_year}` : ''}</div>
                `;
                eduContainer.appendChild(div);
            });
        } else {
            eduContainer.innerHTML = '<span style="color: var(--text-muted)">No education history</span>';
        }

        resultsContainer.appendChild(clone);
    });
}

function getInitials(name) {
    if (!name) return '?';
    const parts = name.trim().split(' ');
    if (parts.length === 1) return parts[0].charAt(0).toUpperCase();
    return (parts[0].charAt(0) + parts[parts.length - 1].charAt(0)).toUpperCase();
}
