"use client";

import { useEffect, useState } from "react";
import { Users, Check, ChevronRight, AlertCircle } from "lucide-react";
import { lecturesAPI, groupsAPI } from "@/lib/api";

interface Team {
    id: string;
    name: string;
    description?: string;
    score?: number;
    membership_score?: number;
    content_score?: number;
    overlap_score?: number;
    reason?: string;
}

const CONTENT_RELEVANCE_THRESHOLD = 10;

function pickDefaultRelevantTeam(teams: Team[]): string[] {
    const transcriptRelevant = teams
        .filter((team) => (team.content_score || 0) >= CONTENT_RELEVANCE_THRESHOLD)
        .sort((a, b) => (b.content_score || 0) - (a.content_score || 0));

    // Auto-select exactly one team only when transcript relevance is strong enough.
    if (transcriptRelevant.length > 0) {
        return [transcriptRelevant[0].id];
    }

    // No confident relevance match: let user choose manually.
    return [];
}

interface TeamSuggestionModalProps {
    lectureId: string;
    orgId?: string;
    isOpen: boolean;
    onClose: () => void;
    onConfirm: (selectedTeamIds: string[]) => void;
}

export default function TeamSuggestionModal({
    lectureId,
    orgId,
    isOpen,
    onClose,
    onConfirm,
}: TeamSuggestionModalProps) {
    const [suggestedTeams, setSuggestedTeams] = useState<Team[]>([]);
    const [otherTeams, setOtherTeams] = useState<Team[]>([]);
    const [selectedTeams, setSelectedTeams] = useState<Set<string>>(new Set());
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState("");
    const [confirming, setConfirming] = useState(false);

    useEffect(() => {
        if (!isOpen) return;

        const fetchSuggestedTeams = async () => {
            setLoading(true);
            setError("");
            try {
                const [suggestedRes, allTeamsRes] = await Promise.all([
                    lecturesAPI.suggestTeams(lectureId),
                    orgId ? groupsAPI.listByOrg(orgId) : Promise.resolve({ data: [] }),
                ]);

                const data = suggestedRes.data;
                const teams: Team[] = data.suggested_teams || [];
                setSuggestedTeams(teams);

                const suggestedIds = new Set(teams.map((t) => t.id));
                const backendEligible = Array.isArray(data.eligible_teams) ? data.eligible_teams : [];
                const fallbackEligible = Array.isArray(allTeamsRes.data) ? allTeamsRes.data : [];
                const eligibleSource = backendEligible.length > 0 ? backendEligible : fallbackEligible;

                const allTeams: Team[] = eligibleSource.map((t: any) => ({
                    id: t.id,
                    name: t.name,
                    description: t.description,
                    reason: "Manual override",
                }));
                setOtherTeams(allTeams.filter((t) => !suggestedIds.has(t.id)));

                const preSelectedTeams = new Set<string>(
                    pickDefaultRelevantTeam(teams)
                );
                setSelectedTeams(preSelectedTeams);
            } catch (err) {
                setError(err instanceof Error ? err.message : "Failed to load suggestions");
            } finally {
                setLoading(false);
            }
        };

        fetchSuggestedTeams();
    }, [isOpen, lectureId, orgId]);

    const handleToggleTeam = (teamId: string) => {
        setSelectedTeams((prev) => {
            const newSet = new Set(prev);
            if (newSet.has(teamId)) {
                newSet.delete(teamId);
            } else {
                newSet.add(teamId);
            }
            return newSet;
        });
    };

    const handleConfirm = async () => {
        if (selectedTeams.size === 0) {
            setError("Please select at least one team");
            return;
        }

        setConfirming(true);
        try {
            await lecturesAPI.shareTeams(lectureId, Array.from(selectedTeams));

            onConfirm(Array.from(selectedTeams));
            onClose();
        } catch (err) {
            setError(err instanceof Error ? err.message : "Failed to share lecture");
        } finally {
            setConfirming(false);
        }
    };

    const allTeamIds = [...suggestedTeams, ...otherTeams].map((t) => t.id);
    const allSelected = allTeamIds.length > 0 && allTeamIds.every((id) => selectedTeams.has(id));

    const handleToggleSelectAll = () => {
        if (allTeamIds.length === 0) return;
        if (allSelected) {
            setSelectedTeams(new Set());
            return;
        }
        setSelectedTeams(new Set(allTeamIds));
    };

    if (!isOpen) return null;

    return (
        <div className="modal-overlay" onClick={onClose}>
            <div className="modal-content" onClick={(e) => e.stopPropagation()}>
                <div className="modal-header">
                    <div className="modal-title-section">
                        <Users size={20} style={{ color: "var(--primary-400)" }} />
                        <h2 className="modal-title">Share with Teams</h2>
                    </div>
                    <p className="modal-subtitle">
                        Select which teams in your workspace should have access to this knowledge item
                    </p>
                </div>

                {error && (
                    <div className="alert alert-error" style={{ marginBottom: "16px" }}>
                        <AlertCircle size={16} /> {error}
                    </div>
                )}

                <div className="modal-body">
                    {loading ? (
                        <div
                            style={{
                                display: "flex",
                                alignItems: "center",
                                justifyContent: "center",
                                gap: "12px",
                                minHeight: "200px",
                            }}
                        >
                            <div className="spinner" style={{ width: 24, height: 24 }} />
                            <p>Loading team suggestions...</p>
                        </div>
                    ) : suggestedTeams.length === 0 && otherTeams.length === 0 ? (
                        <div
                            style={{
                                padding: "32px 16px",
                                textAlign: "center",
                                color: "var(--text-muted)",
                            }}
                        >
                            <Users size={32} style={{ opacity: 0.5, marginBottom: "12px" }} />
                            <p>No teams available in your workspace</p>
                        </div>
                    ) : (
                        <>
                            <div style={{ padding: "0 16px 12px" }}>
                                <button
                                    type="button"
                                    className="btn btn-secondary"
                                    onClick={handleToggleSelectAll}
                                    style={{ width: "100%", justifyContent: "center" }}
                                >
                                    {allSelected ? "Unselect all teams" : "Select all teams"}
                                </button>
                            </div>
                            {suggestedTeams.length > 0 && (
                                <div style={{ padding: "0 16px 8px", color: "var(--text-muted)", fontSize: "0.8rem", fontWeight: 600 }}>
                                    Suggested Teams
                                </div>
                            )}
                            {selectedTeams.size === 0 && (
                                <div
                                    style={{
                                        margin: "0 16px 10px",
                                        padding: "10px 12px",
                                        borderRadius: "8px",
                                        border: "1px solid var(--border)",
                                        color: "var(--text-muted)",
                                        fontSize: "0.83rem",
                                    }}
                                >
                                    No strong transcript match found. Please select team(s) manually.
                                </div>
                            )}
                            <div className="team-list">
                                {suggestedTeams.map((team) => {
                                const isSelected = selectedTeams.has(team.id);
                                return (
                                    <div
                                        key={team.id}
                                        className={`team-item ${isSelected ? "selected" : ""}`}
                                        onClick={() => handleToggleTeam(team.id)}
                                    >
                                        <div className="team-checkbox">
                                            {isSelected && (
                                                <Check size={16} style={{ color: "var(--primary)" }} />
                                            )}
                                        </div>

                                        <div className="team-info">
                                            <h3 className="team-name">{team.name}</h3>
                                            {team.description && (
                                                <p className="team-description">{team.description}</p>
                                            )}
                                            {team.reason && (
                                                <p className="team-reason">
                                                    <span className="reason-badge">{team.reason}</span>
                                                </p>
                                            )}
                                        </div>

                                        {team.score !== undefined && (
                                            <div className="team-score">
                                                <div className="score-bar">
                                                    <div
                                                        className="score-fill"
                                                        style={{
                                                            width: `${Math.max(0, Math.min(team.score, 100))}%`,
                                                            background: `hsl(${Math.max(
                                                                0,
                                                                Math.min(120, (Math.max(0, Math.min(team.score, 100)) / 100) * 120)
                                                            )}, 70%, 60%)`,
                                                        }}
                                                    />
                                                </div>
                                                <span className="score-text">{Math.max(0, Math.min(team.score, 100))}%</span>
                                            </div>
                                        )}

                                        <ChevronRight size={16} style={{ opacity: 0.5 }} />
                                    </div>
                                );
                                })}
                            </div>

                            {otherTeams.length > 0 && (
                                <>
                                    <div style={{ padding: "10px 16px 8px", color: "var(--text-muted)", fontSize: "0.8rem", fontWeight: 600 }}>
                                        Other Eligible Teams (Manual)
                                    </div>
                                    <div className="team-list">
                                        {otherTeams.map((team) => {
                                            const isSelected = selectedTeams.has(team.id);
                                            return (
                                                <div
                                                    key={team.id}
                                                    className={`team-item ${isSelected ? "selected" : ""}`}
                                                    onClick={() => handleToggleTeam(team.id)}
                                                >
                                                    <div className="team-checkbox">
                                                        {isSelected && (
                                                            <Check size={16} style={{ color: "var(--primary)" }} />
                                                        )}
                                                    </div>

                                                    <div className="team-info">
                                                        <h3 className="team-name">{team.name}</h3>
                                                        {team.description && (
                                                            <p className="team-description">{team.description}</p>
                                                        )}
                                                        <p className="team-reason">
                                                            <span className="reason-badge" style={{ backgroundColor: "rgba(16, 185, 129, 0.2)", color: "#34d399" }}>
                                                                Manual override
                                                            </span>
                                                        </p>
                                                    </div>

                                                    <ChevronRight size={16} style={{ opacity: 0.5 }} />
                                                </div>
                                            );
                                        })}
                                    </div>
                                </>
                            )}
                        </>
                    )}
                </div>

                <div className="modal-footer">
                    <button
                        className="btn btn-secondary"
                        onClick={onClose}
                        disabled={confirming}
                    >
                        Cancel
                    </button>
                    <button
                        className="btn btn-primary"
                        onClick={handleConfirm}
                        disabled={confirming || selectedTeams.size === 0}
                    >
                        {confirming ? (
                            <>
                                <div
                                    className="spinner"
                                    style={{
                                        width: 14,
                                        height: 14,
                                        borderWidth: 2,
                                    }}
                                />
                                Sharing...
                            </>
                        ) : (
                            <>
                                <Check size={16} /> Share ({selectedTeams.size})
                            </>
                        )}
                    </button>
                </div>
            </div>

            <style jsx>{`
                .modal-overlay {
                    position: fixed;
                    top: 0;
                    left: 0;
                    right: 0;
                    bottom: 0;
                    background: rgba(0, 0, 0, 0.6);
                    display: flex;
                    align-items: center;
                    justify-content: center;
                    z-index: 1000;
                    animation: fadeIn 0.3s ease;
                }

                .modal-content {
                    background: var(--bg-card);
                    border-radius: 12px;
                    max-width: 500px;
                    width: 90%;
                    max-height: 80vh;
                    overflow-y: auto;
                    box-shadow: 0 20px 60px rgba(0, 0, 0, 0.5);
                    animation: slideUp 0.3s ease;
                }

                .modal-header {
                    padding: 24px;
                    border-bottom: 1px solid var(--border);
                }

                .modal-title-section {
                    display: flex;
                    align-items: center;
                    gap: 12px;
                    margin-bottom: 8px;
                }

                .modal-title {
                    margin: 0;
                    font-size: 1.25rem;
                    font-weight: 600;
                    color: var(--text-primary);
                }

                .modal-subtitle {
                    margin: 0;
                    font-size: 0.9rem;
                    color: var(--text-muted);
                }

                .modal-body {
                    padding: 16px 0;
                    min-height: 200px;
                }

                .team-list {
                    display: flex;
                    flex-direction: column;
                    gap: 2px;
                }

                .team-item {
                    padding: 12px 16px;
                    display: flex;
                    align-items: center;
                    gap: 12px;
                    cursor: pointer;
                    transition: background-color 0.2s ease;
                    border-left: 3px solid transparent;
                }

                .team-item:hover {
                    background-color: var(--bg-elevated);
                }

                .team-item.selected {
                    background-color: rgba(59, 130, 246, 0.1);
                    border-left-color: var(--primary);
                }

                .team-checkbox {
                    width: 24px;
                    height: 24px;
                    border-radius: 6px;
                    border: 2px solid var(--border);
                    display: flex;
                    align-items: center;
                    justify-content: center;
                    flex-shrink: 0;
                    transition: all 0.2s ease;
                }

                .team-item.selected .team-checkbox {
                    background-color: var(--primary);
                    border-color: var(--primary);
                }

                .team-info {
                    flex: 1;
                }

                .team-name {
                    margin: 0;
                    font-size: 0.95rem;
                    font-weight: 500;
                    color: var(--text-primary);
                }

                .team-description {
                    margin: 4px 0 0 0;
                    font-size: 0.85rem;
                    color: var(--text-muted);
                }

                .team-reason {
                    margin: 6px 0 0 0;
                }

                .reason-badge {
                    display: inline-block;
                    padding: 2px 8px;
                    background-color: rgba(59, 130, 246, 0.2);
                    color: var(--primary-300);
                    font-size: 0.75rem;
                    border-radius: 4px;
                    font-weight: 500;
                }

                .team-score {
                    display: flex;
                    align-items: center;
                    gap: 8px;
                }

                .score-bar {
                    width: 60px;
                    height: 4px;
                    background-color: var(--border);
                    border-radius: 2px;
                    overflow: hidden;
                }

                .score-fill {
                    height: 100%;
                    transition: width 0.3s ease;
                }

                .score-text {
                    font-size: 0.75rem;
                    color: var(--text-muted);
                    font-weight: 500;
                    white-space: nowrap;
                    width: 30px;
                    text-align: right;
                }

                .modal-footer {
                    padding: 16px 24px;
                    border-top: 1px solid var(--border);
                    display: flex;
                    gap: 12px;
                    justify-content: flex-end;
                }

                .btn {
                    display: inline-flex;
                    align-items: center;
                    gap: 8px;
                    padding: 8px 16px;
                    border-radius: 6px;
                    border: none;
                    cursor: pointer;
                    font-size: 0.9rem;
                    font-weight: 500;
                    transition: all 0.2s ease;
                }

                .btn:disabled {
                    opacity: 0.5;
                    cursor: not-allowed;
                }

                .btn-secondary {
                    background-color: var(--bg-elevated);
                    color: var(--text-primary);
                }

                .btn-secondary:hover:not(:disabled) {
                    background-color: var(--border);
                }

                .btn-primary {
                    background-color: var(--primary);
                    color: white;
                }

                .btn-primary:hover:not(:disabled) {
                    background-color: var(--primary-600);
                }

                @keyframes fadeIn {
                    from {
                        opacity: 0;
                    }
                    to {
                        opacity: 1;
                    }
                }

                @keyframes slideUp {
                    from {
                        transform: translateY(20px);
                        opacity: 0;
                    }
                    to {
                        transform: translateY(0);
                        opacity: 1;
                    }
                }
            `}</style>
        </div>
    );
}
